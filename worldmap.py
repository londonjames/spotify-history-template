#!/usr/bin/env python3
"""Make web/land.json: the world's land as a grid of dots, for the map on a
day's page.

Natural Earth's 1:110m land outline (public domain) is drawn at eight times
the grid and a cell is land when a third of it is. Run by hand, once; the
result is committed and ./refresh does not need the network for it.

    python3 worldmap.py
"""
import json
import os
import requests
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
       "master/geojson/ne_110m_land.geojson")
STEP = 2.5                    # degrees per dot
TOP, BOTTOM = 80.0, -57.5     # no ice cap, no Antarctica
K = 8


def main():
    geo = requests.get(SRC, timeout=60).json()
    w, h = int(360 / STEP), int((TOP - BOTTOM) / STEP)
    im = Image.new("L", (w * K, h * K), 0)
    dr = ImageDraw.Draw(im)
    xy = lambda lon, lat: ((lon + 180) / STEP * K, (TOP - lat) / STEP * K)
    for f in geo["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            dr.polygon([xy(*p[:2]) for p in poly[0]], fill=255)
            for hole in poly[1:]:
                dr.polygon([xy(*p[:2]) for p in hole], fill=0)
    small = im.resize((w, h), Image.BOX)
    rows = []
    for y in range(h):
        bits = "".join("1" if small.getpixel((x, y)) > 85 else "0" for x in range(w))
        rows.append(f"{int(bits, 2):0{w // 4}x}")
    out = {"w": w, "h": h, "step": STEP, "top": TOP, "rows": rows}
    json.dump(out, open(os.path.join(HERE, "web", "land.json"), "w"), separators=(",", ":"))
    print(f"web/land.json  {w}x{h} dots, {sum(bin(int(r, 16)).count('1') for r in rows):,} on land")


if __name__ == "__main__":
    main()
