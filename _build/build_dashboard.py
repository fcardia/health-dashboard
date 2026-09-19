#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds a self-contained HTML dashboard from every weekly Zepp/Mi-Fit export
folder (w_DDMM_DDMM) sitting in this project root.

Run it again after dropping in a new week folder: it rescans everything,
merges and de-duplicates, then rewrites dashboard.html.

A note on clocks, because this export is inconsistent about them:

  * ACTIVITY, ACTIVITY_MINUTE, ACTIVITY_STAGE, HEARTRATE_AUTO and SLEEP_MINUTE
    use separate date + time columns holding LOCAL wall-clock time. Verified:
    the hr column in SLEEP_MINUTE matches HEARTRATE_AUTO exactly, minute for
    minute, with no shift.
  * SPORT, BODY and HEARTRATE carry "+0000" timestamps that really are UTC.
  * SLEEP carries "+0000" timestamps whose time-of-day is UTC but whose DATE is
    one day early, and its own date column inherits that.

Rather than hardcode an offset, the builder learns it: nightly stage durations
in SLEEP are matched against the sessions reconstructed from SLEEP_MINUTE, and
the difference between the two start times gives the correction. The whole-day
part of it places SLEEP's rows; the remainder is the UTC offset applied to
SPORT, BODY and HEARTRATE. If the export is ever fixed upstream the learned
offset simply becomes zero and nothing here changes.
"""

import csv
import io
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "_build")
OUT = os.path.join(ROOT, "dashboard.html")
TEMPLATE = os.path.join(BUILD, "template.html")

# Second output: the installable app. Same template, but the data marker is left
# alone and data-loader.js fills it from the phone's IndexedDB instead. Only the
# shell is written to APP_DIR - no personal data, so it is safe to publish.
APP_SRC = os.path.join(BUILD, "app")
# Named "docs" because GitHub Pages can only serve a branch's root or /docs -
# no other folder name is offered. Nothing to configure beyond picking it.
APP_DIR = os.path.join(ROOT, "docs")
# Everything that is actually data lives here: the raw weekly exports and the
# payload built from them. Kept out of the root so the root holds only the
# things you open or run. Never published - see .gitignore.
DATA_DIR = os.path.join(ROOT, "data")
DATA_OUT = os.path.join(DATA_DIR, "data.json")
APP_ASSETS = ("data-loader.js", "idb-lite.js", "manifest.webmanifest",
              "icon-192.png", "icon-512.png", "icon.svg")

# Optional: point this at your Google Drive for Desktop folder and data.json is
# copied there on every build, ready to pick up from the Drive app on the phone.
#     set HEALTH_DRIVE_DIR=G:\My Drive\health
DRIVE_ENV = "HEALTH_DRIVE_DIR"

WEEK_RE = re.compile(r"^w_(\d{2})(\d{2})_(\d{2})(\d{2})$", re.I)
EPOCH = date(2000, 1, 1)

# Zepp sport type codes -> human names.
SPORT_TYPES = {
    1: "Outdoor running", 2: "Treadmill", 3: "Walking", 4: "Hiking",
    5: "Trail running", 6: "Walking", 7: "Indoor running", 8: "Outdoor cycling",
    9: "Indoor cycling", 10: "Elliptical", 11: "Rowing", 12: "Pool swimming",
    14: "Open-water swim", 15: "Skiing", 16: "Free training",
    17: "Yoga", 18: "Jump rope", 21: "Climbing", 22: "Strength training",
    23: "Football", 24: "Basketball", 60: "Tennis",
}

STAGE_IDX = {"DEEP": 0, "REM": 1, "LIGHT": 2, "WAKE": 3}
NAP_MAX = 90        # a session shorter than this is a nap, not a night
GAP_SPLIT = 60      # minutes of missing data that end a sleep session


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def num(v, cast=float):
    if v is None:
        return None
    v = str(v).strip()
    if v == "" or v.lower() in ("null", "none", "nan"):
        return None
    try:
        return cast(float(v))
    except ValueError:
        return None


def hhmm_to_min(s):
    if not s:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", str(s).strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def parse_ts(s):
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", str(s).strip())
    if not m:
        return None
    g = [int(x) for x in m.groups()[:5]]
    return datetime(g[0], g[1], g[2], g[3], g[4])


def read_csv(path):
    with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            yield dict((k.strip() if k else k, v) for k, v in row.items())


def pctl(vals_sorted, p):
    if not vals_sorted:
        return None
    k = (len(vals_sorted) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(vals_sorted) - 1)
    return vals_sorted[lo] + (vals_sorted[hi] - vals_sorted[lo]) * (k - lo)


def r1(x):
    return None if x is None else round(x, 1)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / float(len(xs)) if xs else None


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    h = len(xs) // 2
    return xs[h] if len(xs) % 2 else (xs[h - 1] + xs[h]) / 2.0


def dstr(d):
    return d.strftime("%Y-%m-%d")


def dparse(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def abs_min(date_str, minute):
    """Minutes since EPOCH, from a local date string + minute-of-day."""
    return (dparse(date_str) - EPOCH).days * 1440 + minute


def abs_from_dt(dt):
    return (dt.date() - EPOCH).days * 1440 + dt.hour * 60 + dt.minute


def abs_parts(a):
    a = int(round(a))
    return dstr(EPOCH + timedelta(days=a // 1440)), a % 1440


def signed_tz(offset):
    """Strip the whole-day part off a learned offset, leaving the UTC shift."""
    if offset is None:
        return 0
    return int(((int(round(offset)) + 720) % 1440) - 720)


def clock(a):
    d, m = abs_parts(a)
    return "%s %02d:%02d" % (d, m // 60, m % 60)


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------
def week_files(path, kind):
    """Every CSV under <week>/<KIND>/ plus a loose <week>/<KIND>.csv."""
    out = []
    sub = os.path.join(path, kind)
    if os.path.isdir(sub):
        for f in sorted(os.listdir(sub)):
            if f.lower().endswith(".csv"):
                out.append(os.path.join(sub, f))
    loose = os.path.join(path, kind + ".csv")
    if os.path.isfile(loose):
        out.append(loose)
    return out


def first_date(path):
    """Earliest date inside a week folder, used to order the exports."""
    best = None
    for kind in ("ACTIVITY", "HEARTRATE_AUTO", "ACTIVITY_MINUTE", "SLEEP"):
        for p in week_files(path, kind):
            for r in read_csv(p):
                d = (r.get("date") or "").strip()
                if re.match(r"^\d{4}-\d{2}-\d{2}$", d) and (best is None or d < best):
                    best = d
            if best:
                return best
    return best


def find_weeks():
    """Weekly export folders, from data/ first and then the root.

    The root is still scanned so an export dropped in the old place keeps
    working; a folder present in both is taken from data/ only.
    """
    out, seen = [], set()
    for base in (DATA_DIR, ROOT):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            full = os.path.join(base, name)
            if name in seen or not os.path.isdir(full) or not WEEK_RE.match(name):
                continue
            seen.add(name)
            out.append({"folder": name, "path": full, "stray": base is ROOT,
                        "first": first_date(full) or "9999"})
    out.sort(key=lambda w: (w["first"], w["folder"]))
    return out


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
class Store(object):
    def __init__(self):
        self.activity = {}    # date -> row
        self.stages = {}      # (date,start,stop) -> row
        self.act_min = {}     # (date,minute) -> steps
        self.hr_auto = {}     # (date,minute) -> bpm
        self.hr_spot = {}     # utc ts -> (bpm, week)
        self.sleep = {}       # summary date -> row
        self.sleep_min = {}   # (date,minute) -> (stage,hr,rr,week)
        self.sport = {}       # utc ts -> row
        self.body = {}        # utc ts -> row
        self.health = {}      # (date,time) -> row
        self.user = None
        self.counts = defaultdict(lambda: defaultdict(int))
        self.origin = {}      # local date -> week folder


def load_week(st, wk):
    f, path = wk["folder"], wk["path"]
    c = st.counts[f]
    mark = lambda d: st.origin.__setitem__(d, f)

    for p in week_files(path, "ACTIVITY"):
        for r in read_csv(p):
            d = (r.get("date") or "").strip()
            if not d:
                continue
            st.activity[d] = {
                "steps": num(r.get("steps"), int) or 0,
                "distance": num(r.get("distance"), int) or 0,
                "runDistance": num(r.get("runDistance"), int) or 0,
                "calories": num(r.get("calories"), int) or 0,
            }
            mark(d)
            c["ACTIVITY"] += 1

    for p in week_files(path, "ACTIVITY_STAGE"):
        for r in read_csv(p):
            d = (r.get("date") or "").strip()
            s, e = hhmm_to_min(r.get("start")), hhmm_to_min(r.get("stop"))
            if not d or s is None or e is None:
                continue
            st.stages[(d, s, e)] = {
                "date": d, "start": s, "stop": e,
                "distance": num(r.get("distance"), int) or 0,
                "calories": num(r.get("calories"), int) or 0,
                "steps": num(r.get("steps"), int) or 0,
            }
            mark(d)
            c["ACTIVITY_STAGE"] += 1

    for p in week_files(path, "ACTIVITY_MINUTE"):
        for r in read_csv(p):
            d, t = (r.get("date") or "").strip(), hhmm_to_min(r.get("time"))
            if not d or t is None:
                continue
            st.act_min[(d, t)] = num(r.get("steps"), int) or 0
            mark(d)
            c["ACTIVITY_MINUTE"] += 1

    for p in week_files(path, "HEARTRATE_AUTO"):
        for r in read_csv(p):
            d, t = (r.get("date") or "").strip(), hhmm_to_min(r.get("time"))
            hr = num(r.get("heartRate"), int)
            if not d or t is None or not hr:
                continue
            st.hr_auto[(d, t)] = hr
            mark(d)
            c["HEARTRATE_AUTO"] += 1

    for p in week_files(path, "HEARTRATE"):
        for r in read_csv(p):
            ts, hr = parse_ts(r.get("time")), num(r.get("heartRate"), int)
            if not ts or not hr:
                continue
            st.hr_spot[ts.strftime("%Y-%m-%d %H:%M:%S")] = (hr, f)
            c["HEARTRATE"] += 1

    for p in week_files(path, "SLEEP"):
        for r in read_csv(p):
            d = (r.get("date") or "").strip()
            if not d:
                continue
            deep = num(r.get("deepSleepTime"), int) or 0
            light = num(r.get("shallowSleepTime"), int) or 0
            rem = num(r.get("REMTime"), int) or 0
            wake = num(r.get("wakeTime"), int) or 0
            if deep + light + rem == 0:
                continue  # placeholder row -- nothing was recorded that night
            start, stop = parse_ts(r.get("start")), parse_ts(r.get("stop"))
            if not start or not stop:
                continue
            st.sleep[d] = {
                "deep": deep, "light": light, "rem": rem, "wake": wake,
                "a0": abs_from_dt(start), "a1": abs_from_dt(stop), "week": f,
            }
            c["SLEEP"] += 1

    for p in week_files(path, "SLEEP_MINUTE"):
        for r in read_csv(p):
            d, t = (r.get("date") or "").strip(), hhmm_to_min(r.get("time"))
            if not d or t is None:
                continue
            st.sleep_min[(d, t)] = (
                (r.get("stage") or "").strip().upper(),
                num(r.get("hr"), int),
                num(r.get("respiratory_rate")),
                f,
            )
            mark(d)
            c["SLEEP_MINUTE"] += 1

    for p in week_files(path, "SPORT"):
        for r in read_csv(p):
            ts = parse_ts(r.get("startTime"))
            if not ts:
                continue
            typ = num(r.get("type"), int) or 0
            st.sport[ts.strftime("%Y-%m-%d %H:%M:%S")] = {
                "type": typ,
                "typeName": SPORT_TYPES.get(typ, "Activity #%d" % typ),
                "a0": abs_from_dt(ts), "week": f,
                "dur": num(r.get("sportTime(s)"), int) or 0,
                "dist": num(r.get("distance(m)")) or 0,
                "cal": num(r.get("calories(kcal)")) or 0,
                "avgPace": num(r.get("avgPace(/meter)")) or 0,
                "maxPace": num(r.get("maxPace(/meter)")) or 0,
            }
            c["SPORT"] += 1

    for p in week_files(path, "BODY"):
        for r in read_csv(p):
            ts = parse_ts(r.get("time"))
            if not ts:
                continue
            st.body[ts.strftime("%Y-%m-%d %H:%M:%S")] = {
                "a0": abs_from_dt(ts), "week": f,
                "weight": num(r.get("weight")), "height": num(r.get("height")),
                "bmi": num(r.get("bmi")), "fatRate": num(r.get("fatRate")),
                "bodyWaterRate": num(r.get("bodyWaterRate")),
                "boneMass": num(r.get("boneMass")),
                "metabolism": num(r.get("metabolism")),
                "muscleRate": num(r.get("muscleRate")),
                "visceralFat": num(r.get("visceralFat")),
            }
            c["BODY"] += 1

    for p in week_files(path, "HEALTH_DATA"):
        for r in read_csv(p):
            d = (r.get("date") or "").strip()
            if not d:
                continue
            t = (r.get("time") or "").strip()
            st.health[(d, t)] = {
                "date": d, "time": t,
                "arm": num(r.get("arm")), "calf": num(r.get("calf")),
                "chest": num(r.get("chest")), "hip": num(r.get("hip")),
                "thigh": num(r.get("thigh")), "waist": num(r.get("waist")),
            }
            c["HEALTH_DATA"] += 1

    for p in week_files(path, "USER"):
        for r in read_csv(p):
            st.user = {
                "userId": (r.get("userId") or "").strip(),
                "gender": num(r.get("gender"), int),
                "height": num(r.get("height")),
                "weight": num(r.get("weight")),
                "nickName": (r.get("nickName") or "").strip(),
                "birthday": (r.get("birthday") or "").strip(),
            }
            c["USER"] += 1


# --------------------------------------------------------------------------
# sleep: sessions, learned clock correction, nights
# --------------------------------------------------------------------------
def sleep_sessions(st):
    """Contiguous runs of SLEEP_MINUTE rows. These carry the correct clock."""
    keys = sorted(st.sleep_min.keys(), key=lambda k: (k[0], k[1]))
    if not keys:
        return []
    groups, cur, prev = [], [], None
    for k in keys:
        a = abs_min(k[0], k[1])
        if prev is not None and a - prev > GAP_SPLIT:
            groups.append(cur)
            cur = []
        cur.append((a, k))
        prev = a
    if cur:
        groups.append(cur)

    out = []
    for grp in groups:
        a0, a1 = grp[0][0], grp[-1][0]
        counts, minutes, hrs, weeks = [0, 0, 0, 0], [], [], defaultdict(int)
        for a, k in grp:
            stage, hr, rr, wf = st.sleep_min[k]
            si = STAGE_IDX.get(stage, 2)
            counts[si] += 1
            minutes.append([a - a0, si, hr])
            weeks[wf] += 1
            if hr:
                hrs.append(hr)
        out.append({
            "a0": a0, "a1": a1, "span": a1 - a0 + 1,
            "deep": counts[0], "rem": counts[1], "light": counts[2], "wake": counts[3],
            "asleep": counts[0] + counts[1] + counts[2],
            "hrAvg": r1(mean(hrs)), "hrMin": min(hrs) if hrs else None,
            "minutes": minutes,
            "week": max(weeks.items(), key=lambda x: x[1])[0] if weeks else None,
        })
    return out


def learn_offsets(st, sessions):
    """
    Match SLEEP rows to reconstructed sessions by their stage-duration
    fingerprint; the start-time difference is the correction SLEEP needs.

    Fingerprints do repeat once enough weeks pile up, so collisions are
    resolved rather than discarded: every candidate offset is pooled, the most
    common value wins, and each row then takes the candidate nearest to it.
    Returned per export folder, with a global median as the fallback.
    """
    by_tuple = defaultdict(list)
    for s in sessions:
        by_tuple[(s["deep"], s["light"], s["rem"], s["wake"])].append(s)

    rows = []
    pool = defaultdict(int)
    for d, row in sorted(st.sleep.items()):
        cands = by_tuple.get((row["deep"], row["light"], row["rem"], row["wake"]))
        if not cands:
            continue
        offs = [c["a0"] - row["a0"] for c in cands]
        rows.append((row, offs))
        for o in offs:
            pool[o] += 1
    if not rows:
        return {}, None, 0

    # most frequent offset, ties broken towards the smallest correction
    anchor = sorted(pool.items(), key=lambda kv: (-kv[1], abs(kv[0])))[0][0]

    per_week, everything = defaultdict(list), []
    for row, offs in rows:
        off = min(offs, key=lambda o: abs(o - anchor))
        if abs(off - anchor) > 120:      # further than a DST step -- not evidence
            continue
        per_week[row["week"]].append(off)
        everything.append(off)

    glob = median(everything)
    learned = dict((w, median(v)) for w, v in per_week.items())
    return learned, glob, len(everything)


def build_nights(st, sessions, learned, glob):
    """
    Nights keyed by the LOCAL date they end on, which is the convention the
    watch itself uses. SLEEP supplies the stage durations, SLEEP_MINUTE the
    hypnogram; either can stand alone.
    """
    def off_for(week):
        v = learned.get(week)
        if v is None:
            v = glob
        return int(round(v)) if v is not None else 0

    nights, used = {}, set()

    # 1. every SLEEP row, shifted onto the local clock
    for d, row in sorted(st.sleep.items()):
        off = off_for(row["week"])
        a0, a1 = row["a0"] + off, row["a1"] + off
        nd = abs_parts(a1)[0]
        total = row["deep"] + row["light"] + row["rem"]
        nights[nd] = {
            "deep": row["deep"], "rem": row["rem"], "light": row["light"],
            "wake": row["wake"], "total": total, "inBed": total + row["wake"],
            "a0": a0, "a1": a1, "src": "summary", "week": row["week"],
        }

    # 2. attach minute detail to whichever night it overlaps
    for i, s in enumerate(sessions):
        if s["span"] < NAP_MAX:
            continue
        best, bestOv = None, 0
        for nd, n in nights.items():
            ov = min(s["a1"], n["a1"]) - max(s["a0"], n["a0"])
            if ov > bestOv:
                best, bestOv = nd, ov
        if best is not None and bestOv > s["span"] * 0.4:
            nights[best]["hypno"] = s["minutes"]
            nights[best]["hypnoA0"] = s["a0"]
            nights[best]["hrAvg"] = s["hrAvg"]
            nights[best]["hrMin"] = s["hrMin"]
            used.add(i)

    # 3. sessions with no SLEEP row of their own become nights in their own right
    for i, s in enumerate(sessions):
        if i in used or s["span"] < NAP_MAX:
            continue
        nd = abs_parts(s["a1"])[0]
        if nd in nights:
            continue
        nights[nd] = {
            "deep": s["deep"], "rem": s["rem"], "light": s["light"],
            "wake": s["wake"], "total": s["asleep"], "inBed": s["span"],
            "a0": s["a0"], "a1": s["a1"], "src": "minutes", "week": s["week"],
            "hypno": s["minutes"], "hypnoA0": s["a0"],
            "hrAvg": s["hrAvg"], "hrMin": s["hrMin"],
        }

    # 4. short sessions are naps, filed under the day they start
    naps = defaultdict(list)
    for s in sessions:
        if s["span"] >= NAP_MAX:
            continue
        d, m = abs_parts(s["a0"])
        naps[d].append([m, s["span"], s["asleep"]])

    # 5. finish each night: clock fields relative to midnight
    for nd, n in nights.items():
        n["eff"] = r1(100.0 * n["total"] / n["inBed"]) if n["inBed"] else None
        bd, bm = abs_parts(n["a0"])
        n["bedMin"] = bm - 1440 if bm >= 720 else bm   # negative = before midnight
        n["wakeMin"] = abs_parts(n["a1"])[1]
        n["start"] = clock(n["a0"])
        n["stop"] = clock(n["a1"])
        n.pop("a0", None)
        n.pop("a1", None)
        if "hypnoA0" in n:
            n["hypnoStart"] = abs_parts(n.pop("hypnoA0"))[1]

    return nights, naps


# --------------------------------------------------------------------------
# derive days
# --------------------------------------------------------------------------
def hr_zones(readings, max_hr):
    """readings: [(minute,bpm)] sorted. Each is weighted by the gap to the next."""
    cuts = [max_hr * b for b in (0.50, 0.70, 0.80, 0.90)]
    z = [0.0] * 5
    for i, (m, hr) in enumerate(readings):
        gap = 1.0
        if i + 1 < len(readings):
            gap = min(max(readings[i + 1][0] - m, 1), 10)
        k = 0
        while k < 4 and hr >= cuts[k]:
            k += 1
        z[k] += gap
    return [int(round(v)) for v in z]


def build_days(st, max_hr, nights, naps, sports_local):
    hr_by_date = defaultdict(list)
    for (d, m), hr in st.hr_auto.items():
        hr_by_date[d].append((m, hr))
    steps_by_date = defaultdict(list)
    for (d, m), s in st.act_min.items():
        if s:
            steps_by_date[d].append((m, s))
    bouts_by_date = defaultdict(list)
    for b in st.stages.values():
        bouts_by_date[b["date"]].append(b)
    sport_by_date = defaultdict(list)
    for s in sports_local:
        sport_by_date[s["date"]].append(s)

    all_dates = (set(st.activity) | set(nights) | set(naps) | set(hr_by_date) |
                 set(steps_by_date) | set(bouts_by_date) | set(sport_by_date))
    if not all_dates:
        return []

    lo, hi = min(all_dates), max(all_dates)
    span = (dparse(hi) - dparse(lo)).days
    dates = [dstr(dparse(lo) + timedelta(days=i)) for i in range(span + 1)]

    days = []
    for d in dates:
        a = st.activity.get(d)
        hrs = sorted(hr_by_date.get(d, []))
        vals = sorted(h for _, h in hrs)
        night = nights.get(d)

        zones = hr_zones(hrs, max_hr) if hrs else [0] * 5
        wear = sum(zones)
        # A day the watch was barely worn can't be compared with a full one.
        partial = bool(hrs) and wear < 600

        # resting HR: the calmest tenth of the night. Failing that, the 5th
        # percentile of a full day's readings -- never of a sliver of one.
        rest = None
        if night and night.get("hypno"):
            nh = sorted(h for _, _, h in night["hypno"] if h)
            if len(nh) >= 10:
                rest = mean(nh[:max(1, len(nh) // 10)])
        if rest is None and not partial and len(vals) >= 200:
            rest = pctl(vals, 0.05)

        bouts = sorted(bouts_by_date.get(d, []), key=lambda b: b["start"])
        sports = sorted(sport_by_date.get(d, []), key=lambda s: s["startMin"])

        sleep = None
        if night:
            sleep = dict(night)
            sleep["naps"] = sum(x[2] for x in naps.get(d, []))
            sleep.pop("hypno", None)

        days.append({
            "date": d,
            "dow": dparse(d).weekday(),
            "week": st.origin.get(d),
            "hasActivity": a is not None,
            "wearMin": wear,
            "partial": partial,
            "steps": a["steps"] if a else None,
            "distance": a["distance"] if a else None,
            "runDistance": a["runDistance"] if a else None,
            "calories": a["calories"] if a else None,
            "activeMin": sum(b["stop"] - b["start"] for b in bouts),
            "bouts": [[b["start"], b["stop"], b["steps"], b["distance"], b["calories"]]
                      for b in bouts],
            "stepSeries": sorted(steps_by_date.get(d, [])),
            "hrSeries": [[m, h] for m, h in hrs],
            "hr": {
                "n": len(vals),
                "min": min(vals), "max": max(vals), "avg": r1(mean(vals)),
                "p10": r1(pctl(vals, 0.10)), "p90": r1(pctl(vals, 0.90)),
                "rest": r1(rest), "zones": zones,
            } if vals else None,
            "sleep": sleep,
            "hypno": night.get("hypno") if night else None,
            "hypnoStart": night.get("hypnoStart") if night else None,
            "naps": naps.get(d, []),
            "sports": [{k: s[k] for k in
                        ("typeName", "type", "startMin", "dur", "dist", "cal", "avgPace")}
                       for s in sports],
            "sportCal": int(sum(s["cal"] for s in sports)),
            "sportMin": int(round(sum(s["dur"] for s in sports) / 60.0)),
        })
    return days


def build_weeks(st, days, weeks, learned, glob):
    by_week = defaultdict(list)
    for d in days:
        if d["week"]:
            by_week[d["week"]].append(d["date"])
    out = []
    for wk in weeks:
        f = wk["folder"]
        ds = sorted(by_week.get(f, []))
        m = WEEK_RE.match(f)
        label = "%s/%s - %s/%s" % (m.group(1), m.group(2), m.group(3), m.group(4)) if m else f
        off = learned.get(f, glob)
        out.append({
            "folder": f, "label": label,
            "start": ds[0] if ds else None,
            "end": ds[-1] if ds else None,
            "days": len(ds),
            "counts": dict(st.counts.get(f, {})),
            "sleepOffset": int(round(off)) if off is not None else None,
            "tz": signed_tz(off) if off is not None else None,
        })
    out.sort(key=lambda w: (w["start"] or "9999"))
    return out


def calendar_weeks(days):
    buckets = defaultdict(list)
    for d in days:
        iso = dparse(d["date"]).isocalendar()
        buckets[(iso[0], iso[1])].append(d)
    out = []
    for (y, w), ds in sorted(buckets.items()):
        ds.sort(key=lambda x: x["date"])
        ws = [x for x in ds if x["steps"] and not x["partial"]]
        wl = [x for x in ds if x["sleep"]]
        wh = [x for x in ds if x["hr"]]
        out.append({
            "key": "%d-W%02d" % (y, w),
            "start": ds[0]["date"], "end": ds[-1]["date"], "days": len(ds),
            "steps": sum(x["steps"] or 0 for x in ds),
            "avgSteps": int(round(mean([x["steps"] for x in ws]) or 0)),
            "distance": sum(x["distance"] or 0 for x in ds),
            "calories": sum(x["calories"] or 0 for x in ds),
            "activeMin": sum(x["activeMin"] for x in ds),
            "avgSleep": r1(mean([x["sleep"]["total"] for x in wl])),
            "avgDeep": r1(mean([x["sleep"]["deep"] for x in wl])),
            "avgRem": r1(mean([x["sleep"]["rem"] for x in wl])),
            "avgRest": r1(mean([x["hr"]["rest"] for x in wh if x["hr"]["rest"]])),
            "avgHr": r1(mean([x["hr"]["avg"] for x in wh])),
            "workouts": sum(len(x["sports"]) for x in ds),
            "workoutMin": sum(x["sportMin"] for x in ds),
            "workoutCal": sum(x["sportCal"] for x in ds),
        })
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def write_app(html, blob, stamp):
    """Write app/: the same page with DATA left null, plus the static shell.

    Returns the lines to add to the build summary.
    """
    if not os.path.isdir(APP_SRC):
        return ["app/       : skipped (_build/app is missing)"]
    if not os.path.isdir(APP_DIR):
        os.makedirs(APP_DIR)

    # The data marker is deliberately NOT substituted here: data-loader.js fills
    # window.DATA at runtime. The manifest goes next to the viewport meta (the
    # implicit <head>); the scripts go last, after the inline one that defines
    # window.__boot.
    head = ('<meta name="viewport" content="width=device-width,'
            'initial-scale=1,viewport-fit=cover">')
    if head not in html:
        raise RuntimeError("template.html is missing the viewport meta")
    page = html.replace(head, head + "\n"
        '<link rel="manifest" href="manifest.webmanifest">\n'
        '<meta name="theme-color" content="#f9f9f7" media="(prefers-color-scheme: light)">\n'
        '<meta name="theme-color" content="#0d0d0d" media="(prefers-color-scheme: dark)">\n'
        '<link rel="apple-touch-icon" href="icon-192.png">', 1)
    page = (page.rstrip() + '\n<script src="idb-lite.js"></script>'
                            '\n<script src="data-loader.js"></script>\n')
    with io.open(os.path.join(APP_DIR, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)

    for name in APP_ASSETS:
        src = os.path.join(APP_SRC, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(APP_DIR, name))

    # Stamping the cache version is what retires the previous shell cache.
    with io.open(os.path.join(APP_SRC, "sw.js"), "r", encoding="utf-8") as fh:
        sw = fh.read()
    with io.open(os.path.join(APP_DIR, "sw.js"), "w", encoding="utf-8") as fh:
        fh.write(sw.replace("__CACHE_VERSION__", stamp))

    lines = ["App shell  : %s  (cache %s)" % (APP_DIR, stamp)]

    # data.json stays OUT of the published folder on purpose: this is the file
    # with every heartbeat in it.
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR)
    with io.open(DATA_OUT, "w", encoding="utf-8") as fh:
        fh.write(blob)
    lines.append("Data file  : %s  (%.0f KB)"
                 % (DATA_OUT, os.path.getsize(DATA_OUT) / 1024.0))

    drive = os.environ.get(DRIVE_ENV)
    if drive and os.path.isdir(drive):
        shutil.copy2(DATA_OUT, os.path.join(drive, "data.json"))
        lines.append("Drive copy : %s" % drive)
    elif drive:
        lines.append("Drive copy : %s is set but is not a folder - skipped" % DRIVE_ENV)
    return lines


def main():
    weeks = find_weeks()
    if not weeks:
        sys.stderr.write("No week folders (w_DDMM_DDMM) found in %s\n" % DATA_DIR)
        return 1

    st = Store()
    for wk in weeks:
        load_week(st, wk)

    user = st.user or {}
    age = None
    bd = (user.get("birthday") or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})", bd)
    if m:
        by, bm = int(m.group(1)), int(m.group(2))
        today = date.today()
        age = today.year - by - (1 if today.month < bm else 0)
    max_hr = 220 - age if age else 190

    sessions = sleep_sessions(st)
    learned, glob, matched = learn_offsets(st, sessions)
    nights, naps = build_nights(st, sessions, learned, glob)

    # put the UTC-stamped files onto the local clock
    def tz_for(week):
        v = learned.get(week)
        return signed_tz(v if v is not None else glob)

    sports_local = []
    for s in sorted(st.sport.values(), key=lambda x: x["a0"]):
        a = s["a0"] + tz_for(s["week"])
        d, mm = abs_parts(a)
        r = dict(s)
        r.pop("a0")
        r["date"], r["startMin"], r["start"] = d, mm, clock(a)
        sports_local.append(r)

    body_local = []
    for b in sorted(st.body.values(), key=lambda x: x["a0"]):
        a = b["a0"] + tz_for(b["week"])
        r = dict(b)
        r.pop("a0")
        r["time"], r["date"] = clock(a), abs_parts(a)[0]
        body_local.append(r)

    spot_local = []
    for ts, (hr, wf) in st.hr_spot.items():
        a = abs_from_dt(parse_ts(ts)) + tz_for(wf)
        spot_local.append({"time": clock(a), "hr": hr})
    spot_local.sort(key=lambda x: x["time"])

    days = build_days(st, max_hr, nights, naps, sports_local)
    if not days:
        sys.stderr.write("Week folders found but no usable rows inside them.\n")
        return 1

    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "root": os.path.basename(ROOT),
        "user": {
            "name": user.get("nickName") or "You",
            "gender": {1: "Male", 0: "Female"}.get(user.get("gender"), "-"),
            "height": user.get("height"), "weight": user.get("weight"),
            "birthday": bd or None, "age": age, "maxHr": max_hr,
        },
        "zoneCuts": [int(round(max_hr * b)) for b in (0.50, 0.70, 0.80, 0.90)],
        "clock": {
            "matched": matched,
            "sleepOffset": int(round(glob)) if glob is not None else None,
            "tz": signed_tz(glob) if glob is not None else None,
        },
        "weeks": build_weeks(st, days, weeks, learned, glob),
        "isoWeeks": calendar_weeks(days),
        "days": days,
        "sport": sports_local,
        "body": body_local,
        "health": sorted(st.health.values(), key=lambda h: (h["date"], h["time"])),
        "hrSpot": spot_local,
        "totals": {
            "days": len([d for d in days if d["hasActivity"] or d["sleep"] or d["hr"]]),
            "records": sum(sum(c.values()) for c in st.counts.values()),
            "byKind": {},
        },
    }
    for c in st.counts.values():
        for k, v in c.items():
            payload["totals"]["byKind"][k] = payload["totals"]["byKind"].get(k, 0) + v

    with io.open(TEMPLATE, "r", encoding="utf-8") as fh:
        html = fh.read()
    if "/*__DATA__*/null" not in html:
        sys.stderr.write("template.html is missing the /*__DATA__*/null marker\n")
        return 2
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    with io.open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html.replace("/*__DATA__*/null", blob))
    stamp = payload["generated"].replace(" ", "-").replace(":", "")
    app_lines = write_app(html, blob, stamp)

    print("Exports    : %d (%s)" % (len(weeks), ", ".join(w["folder"] for w in weeks)))
    stray = [w["folder"] for w in weeks if w.get("stray")]
    if stray:
        print("             ^ still in the project root, move to data\\: %s"
              % ", ".join(stray))
    print("Days       : %d  %s -> %s" % (len(days), days[0]["date"], days[-1]["date"]))
    print("Nights     : %d   Workouts: %d   Rows: %d"
          % (len(nights), len(sports_local), payload["totals"]["records"]))
    if glob is not None:
        print("Clock      : learned from %d night%s - SLEEP shifted %+d min, "
              "UTC offset %+d min" % (matched, "" if matched == 1 else "s",
                                      int(round(glob)), signed_tz(glob)))
    else:
        print("Clock      : no overlap between SLEEP and SLEEP_MINUTE; "
              "timestamps left as exported")
    print("Written    : %s  (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))
    for line in app_lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
