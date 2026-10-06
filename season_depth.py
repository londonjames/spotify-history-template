#!/usr/bin/env python3
"""Work out near and far in each season picture, into web/seasons/<id>.d.jpg.

The Seasons page moves the foreground of a picture slightly more than its
background while the camera drifts, which needs to know which is which. That
comes from Depth Anything, a free model that runs on this Mac -- no key and no
paid service. Bright is near, dark is far.

Run by hand after season_art.py makes or remakes a picture; a map newer than
its picture is left alone.

    python3 season_depth.py            whatever is missing or out of date
    python3 season_depth.py E5         redo one
"""
import glob
import os
import sys

from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "web", "seasons")
MODEL = "depth-anything/Depth-Anything-V2-Small-hf"


def main():
    want = sys.argv[1:]
    pics = sorted(p for p in glob.glob(os.path.join(DIR, "*.jpg"))
                  if not p.endswith(".d.jpg"))
    todo = []
    for p in pics:
        d = p[:-4] + ".d.jpg"
        sid = os.path.basename(p)[:-4]
        if (sid in want) if want else (
                not os.path.exists(d) or os.path.getmtime(d) < os.path.getmtime(p)):
            todo.append((p, d))
    if not todo:
        return print("  nothing to do")
    from transformers import pipeline
    pipe = pipeline("depth-estimation", model=MODEL)
    for p, d in todo:
        depth = pipe(Image.open(p).convert("RGB"))["depth"].convert("L")
        # Small and softened: a hard edge in the map tears the picture where
        # a near thing meets a far one.
        depth = depth.resize((768, 512), Image.LANCZOS).filter(ImageFilter.GaussianBlur(6))
        depth.save(d, quality=80)
        print(f"  {os.path.basename(d)}: {os.path.getsize(d) // 1024} KB")


if __name__ == "__main__":
    main()
