#!/usr/bin/env python3
"""Drop any season song clip whose address has stopped working.

Spotify's 30-second clip addresses are read once and kept in
preview_cache.json. If one expires, the Seasons page would go silent for that
season with nothing to say so. This asks each kept address for its first byte
and forgets the ones that do not answer with audio; story_data.py, which runs
next, then reads a fresh address for them.

    python3 check_clips.py           before story_data.py: forget dead clips
    python3 check_clips.py --built   after it: every season must have a clip
"""
import json
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "preview_cache.json")
STORY = os.path.join(HERE, "web", "story.json")


def plays(url):
    if not url.startswith("http"):
        return os.path.exists(os.path.join(HERE, "web", url))
    try:
        r = requests.get(url, headers={"Range": "bytes=0-0"}, timeout=30, stream=True)
        return r.status_code in (200, 206) and "audio" in r.headers.get("content-type", "")
    except Exception:
        return False


def main():
    if "--built" in sys.argv:
        bad = [f"{p['id']}: {p['track'].get('t')}" for p in json.load(open(STORY))["phases"]
               if not (p["track"].get("audio") and plays(p["track"]["audio"]))]
        if bad:
            print("Seasons with no working song clip:")
            for b in bad:
                print("  " + b)
            sys.exit(1)
        print("  every season's song clip plays")
        return
    cache = json.load(open(CACHE))
    dead = [k for k, v in cache.items()
            if not k.endswith("#img") and isinstance(v, str) and not plays(v)]
    for k in dead:
        del cache[k]
    if dead:
        json.dump(cache, open(CACHE, "w"), indent=1)
    print(f"  {len(cache) - sum(k.endswith('#img') for k in cache)} clips kept, {len(dead)} forgotten")


if __name__ == "__main__":
    main()
