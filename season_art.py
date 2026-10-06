#!/usr/bin/env python3
"""Generate one mood picture per season into web/seasons/<id>.jpg. Optional.

A generated scene behind each season's title, written from season_art.json.
No text, no logos and no real people in any of them.

This is the one script that costs money: it uses the OpenAI images API with
the key in ~/.openai.env (OPENAI_API_KEY=...), one image per season. It is run
by hand and never by ./refresh. A picture is skipped when its file already
exists, so a rerun only fills gaps. Delete a file to redo it. Run
season_depth.py afterwards for the moving-camera effect, then ./refresh.

    python3 season_art.py            make whatever is missing
    python3 season_art.py S5         redo one
"""
import base64
import json
import concurrent.futures as cf
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "web", "seasons")
MODEL = "gpt-image-2"

STYLE = ("Painterly cinematic editorial illustration, rich atmospheric light, "
         "film grain, wide composition with calm darker space along the bottom "
         "and left for overlaid type. Absolutely no text, letters, numbers, logos, "
         "brand marks, album covers or recognisable real people; any figures are "
         "small, seen from behind or in silhouette.")

# One scene per season id, written by you in season_art.json:
#   {"S1": "A seaside boardwalk at golden hour ...", "S2": "..."}
PROMPTS_FILE = os.path.join(HERE, "season_art.json")
PROMPTS = json.load(open(PROMPTS_FILE)) if os.path.exists(PROMPTS_FILE) else {}


def key():
    for line in open(os.path.expanduser("~/.openai.env")):
        if "OPENAI_API_KEY=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no OPENAI_API_KEY in ~/.openai.env")


def make(sid, k, tries=4):
    path = os.path.join(OUT, f"{sid}.jpg")
    r = requests.post("https://api.openai.com/v1/images/generations", timeout=300,
                      headers={"Authorization": f"Bearer {k}"},
                      json={"model": MODEL, "prompt": PROMPTS[sid] + " " + STYLE,
                            "size": "1536x1024", "quality": "medium", "n": 1,
                            "output_format": "jpeg", "output_compression": 82})
    # The account allows five images a minute; wait it out rather than fail.
    if r.status_code == 429 and tries:
        time.sleep(65)
        return make(sid, k, tries - 1)
    if r.status_code != 200:
        return f"{sid}: {r.status_code} {r.text[:200]}"
    open(path, "wb").write(base64.b64decode(r.json()["data"][0]["b64_json"]))
    return f"{sid}: {os.path.getsize(path) // 1024} KB"


def main():
    os.makedirs(OUT, exist_ok=True)
    want = sys.argv[1:] or [s for s in PROMPTS
                            if not os.path.exists(os.path.join(OUT, f"{s}.jpg"))]
    k = key()
    with cf.ThreadPoolExecutor(2) as ex:
        for line in ex.map(lambda s: make(s, k), want):
            print(" ", line, flush=True)


if __name__ == "__main__":
    main()
