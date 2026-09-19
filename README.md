# Smartwatch health data

Weekly Zepp / Mi Fit exports, plus a dashboard built from all of them at once.

```
personal_data/
├── dashboard.html          <- open this (double-click)
├── Update dashboard.bat    <- run this after adding a week
├── data.json               <- the same data on its own, for the phone app
├── w_0609_1309/            <- one folder per weekly export
│   ├── ACTIVITY/  ACTIVITY_MINUTE/  ACTIVITY_STAGE/
│   ├── HEARTRATE/ HEARTRATE_AUTO/
│   ├── SLEEP/     SLEEP_MINUTE/
│   ├── SPORT/     BODY/  HEALTH_DATA/  USER/
│   └── ...
├── docs/                   <- the installable web app (generated; safe to publish)
├── README.md
└── _build/
    ├── build_dashboard.py  <- the generator
    ├── make_icons.py       <- redraws the app icons (run once, by hand)
    ├── template.html       <- the page itself (charts, layout, styling)
    └── app/                <- app shell sources (loader, service worker, manifest)
```

`dashboard.html` and `docs/` are built from the same `template.html`. The only
difference is where the data comes from: baked into the file for the desktop
one, loaded from the phone's own storage for the app.

## Adding a week

1. Export the new week from Zepp and drop the folder in here, named
   `w_DDMM_DDMM` — the same shape as `w_0609_1309`.
2. Double-click **Update dashboard.bat**.
3. `dashboard.html` is rewritten with every week merged in, and opens.

That's the whole loop. Nothing else needs editing: the date range filter, the
export picker, the step calendar and every chart size themselves to whatever is
present. Days that appear in two exports are de-duplicated with the newer file
winning, so overlapping exports are harmless.

The equivalent without the `.bat`:

```
python _build/build_dashboard.py
```

Needs Python 3 only — no packages to install, no internet. `dashboard.html` is
fully self-contained (the data is embedded in it), so it can be copied or
emailed anywhere and still work offline.

## On your phone

The same dashboard installs as an app. It is a web app, not an APK: you open it
once in Chrome and pick **Add to Home Screen**, and after that it has its own
icon, runs full-screen and works offline.

**One-time setup.** Publish the `docs/` folder with GitHub Pages (Settings -> Pages
-> Deploy from a branch -> `main` + `/docs`; that folder name is the one Pages
offers, which is why it is called that). `docs/` holds only the shell (charts,
layout, code) and never any of your data, so the repo can be public. The `.gitignore` here already
blocks `w_*/`, `data.json` and `dashboard.html`; check `git status` before your
first push anyway. Then open the Pages URL on the phone and add it to the home
screen.

**Every week, after running the `.bat`:**

1. Get `data.json` to the phone. Uncomment `HEALTH_DRIVE_DIR` in
   **Update dashboard.bat** and point it at your Google Drive for Desktop
   folder — the build then drops `data.json` there and Drive syncs it by itself.
2. Open the app and tap **Import**. Android's file picker lists Google Drive as
   a source, so you can take the file straight from there. Or, from the Drive
   app, use **Share → Health Dashboard** and skip the picker entirely.

The data is then stored on the phone, so the app opens offline and keeps working
until the next import. The footer always shows when the data was built and when
it was imported, so old numbers can never quietly look current.

There is deliberately no Google sign-in. A Drive API integration would need an
OAuth client whose refresh tokens expire every 7 days while the project is in
*Testing* — about as often as you would tap Import anyway — and `drive.readonly`
is a restricted scope requiring Google verification to publish.

If you ever want a real `.apk`, [Bubblewrap](https://github.com/GoogleChromeLabs/bubblewrap)
wraps this same app into a Trusted Web Activity without changing anything here.

## What's in the dashboard

| Tab | Contents |
| --- | --- |
| Overview | Headline averages with week-on-week deltas, daily steps vs goal, sleep by stage, resting heart rate, a step calendar, and a week-by-week table |
| Activity | Steps / distance / calories, an hour-by-day step heatmap, your typical daily shape, and every movement bout on a 24-hour clock |
| Heart rate | Daily min–average–max range, the 24-hour profile with a percentile band, time in active zones, and any single day minute by minute |
| Sleep | Duration and stage mix per night, bedtime/wake schedule, sleep versus the next day's steps, and a full hypnogram with heart rate for any night |
| Workouts | Session totals, calories by activity, training time per day, every session listed, and when they happened |
| Body | Weight and BMI, where the BMI sits on the scale, body composition, measurements, and your profile |
| Data | What each export contained, which days hold which kind of data, the clock correction, and how every derived number is calculated |

Every chart has a **Table** toggle showing the same numbers as text, and the
range filter at the top scopes the whole page.

## One thing worth knowing about the export

Zepp is inconsistent about time zones:

- `ACTIVITY*`, `HEARTRATE_AUTO` and `SLEEP_MINUTE` use local wall-clock time.
- `SPORT`, `BODY` and `HEARTRATE` carry real UTC timestamps.
- `SLEEP` rows are stamped in UTC **and dated one day early**.

Left alone, that puts workouts two hours off and sleep a full day off. The
builder doesn't assume a fixed offset — it matches each night's stage durations
in `SLEEP` against the sessions rebuilt from `SLEEP_MINUTE` and measures the
shift from your own data, so it keeps working across daylight-saving changes and
travel. The measured values are shown on the **Data** tab. (The reference point
is the heart rate embedded in `SLEEP_MINUTE`, which matches `HEARTRATE_AUTO`
minute for minute.)
