# -*- coding: utf-8 -*-
"""
Generate the PWA launcher icons.

Pure stdlib (zlib + struct) on purpose: the README promises "no packages to
install", so no Pillow. Run once by hand; build_dashboard.py only copies the
result. Writes icon-192.png, icon-512.png and icon.svg into _build/app/.

    python _build/make_icons.py
"""
import math, os, struct, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app")

BG = (0x2a, 0x78, 0xd6)      # --s1, the dashboard's primary blue
FG = (0xff, 0xff, 0xff)

# A pulse trace in 0..1 space. Maskable icons may be cropped to a circle of 80%
# diameter, so every point here stays inside radius .4 of the centre.
PULSE = [(0.18, 0.52), (0.34, 0.52), (0.41, 0.31),
         (0.50, 0.71), (0.58, 0.45), (0.65, 0.52), (0.82, 0.52)]
STROKE = 0.052               # line width, also in 0..1 space


def _dist_to_seg(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    L = vx * vx + vy * vy
    t = 0.0 if L == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L))
    dx, dy = ax + t * vx - px, ay + t * vy - py
    return math.sqrt(dx * dx + dy * dy)


def render(size):
    """Return RGB rows for the icon at `size` px, antialiased on the stroke."""
    half = STROKE / 2.0
    edge = 1.2 / size                      # ~1px feather, in 0..1 units
    segs = [(PULSE[i][0], PULSE[i][1], PULSE[i + 1][0], PULSE[i + 1][1])
            for i in range(len(PULSE) - 1)]
    rows = []
    for y in range(size):
        py = (y + 0.5) / size
        row = bytearray()
        for x in range(size):
            px = (x + 0.5) / size
            d = min(_dist_to_seg(px, py, a, b, c, e) for a, b, c, e in segs)
            if d <= half - edge:
                a = 1.0
            elif d >= half + edge:
                a = 0.0
            else:
                a = (half + edge - d) / (2 * edge)
            row += bytes(bytearray(
                int(round(BG[i] + (FG[i] - BG[i]) * a)) for i in range(3)))
        rows.append(bytes(row))
    return rows


def write_png(path, size, rows):
    raw = b"".join(b"\x00" + r for r in rows)          # filter byte 0 per scanline

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return len(png)


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" fill="#2a78d6"/>
  <polyline points="%s" fill="none" stroke="#ffffff" stroke-width="%.1f"
            stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    for size in (192, 512):
        p = os.path.join(OUT, "icon-%d.png" % size)
        n = write_png(p, size, render(size))
        print("icon-%d.png  %5.1f KB" % (size, n / 1024.0))
    pts = " ".join("%.0f,%.0f" % (x * 100, y * 100) for x, y in PULSE)
    with open(os.path.join(OUT, "icon.svg"), "w") as fh:
        fh.write(SVG % (pts, STROKE * 100))
    print("icon.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
