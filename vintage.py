#!/usr/bin/env python3
"""Fetch the release year of every recording, so a season has a vintage.

Spotify used to publish genres per artist and no longer does -- every artist in
this history except Raffi now comes back with an empty genre list -- so the
obvious way to characterise what a period sounded like is gone.

Release year is the replacement, and it is arguably better. What matters about a
stretch of listening is often not the genre but the era being reached for: a
month spent in 1985 feels different from a month spent on records that came out
that week, and the difference shows up as a shape rather than a label. The Japan
trip is entirely Nik Kershaw, a-ha, Roxette and Don Henley; the number that says
so is the median release year, 1985, against a lifetime median in the 2000s.

Cached to disk, so this is paid for once. 54,055 recordings at 50 per request.

    python3 vintage.py           fetch anything not yet cached
    python3 vintage.py --apply   write the cached years into spotify.db
"""
import base64
import json
import os
import sqlite3
import sys
import time

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "spotify.db")
CACHE = os.path.join(HERE, ".vintage-cache.json")
ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.spotify")


def token():
    load_dotenv(ENV)
    auth = base64.b64encode(
        f'{os.getenv("SPOTIFY_CLIENT_ID")}:{os.getenv("SPOTIFY_CLIENT_SECRET")}'
        .encode()).decode()
    r = requests.post("https://accounts.spotify.com/api/token",
                      headers={"Authorization": f"Basic {auth}"},
                      data={"grant_type": "client_credentials"}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def main():
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    con = sqlite3.connect(DB)

    if "--apply" in sys.argv:
        have = {r[1] for r in con.execute("PRAGMA table_info(plays)")}
        if "release_year" not in have:
            con.execute("ALTER TABLE plays ADD COLUMN release_year INTEGER")
        con.executemany(
            "UPDATE plays SET release_year=? WHERE track_uri=?",
            [(y, u) for u, y in cache.items() if y])
        con.commit()
        n, lo, hi = con.execute(
            """SELECT COUNT(*), MIN(release_year), MAX(release_year)
               FROM plays WHERE release_year IS NOT NULL""").fetchone()
        print(f"applied: {n:,} plays carry a release year ({lo}-{hi})")
        return

    # Most-played first, so an interrupted run still covers the listening that
    # matters rather than a random slice of the long tail.
    uris = [r[0] for r in con.execute(
        """SELECT track_uri FROM plays
           WHERE kind='track' AND track_uri IS NOT NULL
           GROUP BY track_uri ORDER BY COUNT(*) DESC""")]
    todo = [u for u in uris if u not in cache]
    print(f"{len(cache):,} cached, {len(todo):,} to fetch")
    if not todo:
        return

    H = {"Authorization": f"Bearer {token()}"}
    got = time.time()
    for i in range(0, len(todo), 50):
        batch = todo[i:i + 50]
        if time.time() - got > 2700:              # the token lasts an hour
            H = {"Authorization": f"Bearer {token()}"}; got = time.time()
        ids = ",".join(u.rsplit(":", 1)[-1] for u in batch)
        try:
            r = requests.get("https://api.spotify.com/v1/tracks", headers=H,
                             params={"ids": ids, "market": "US"}, timeout=25)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5)) + 1
                print(f"  rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            if r.status_code == 401:
                H = {"Authorization": f"Bearer {token()}"}; got = time.time()
                continue
            r.raise_for_status()
            for u, t in zip(batch, r.json().get("tracks") or []):
                date = ((t or {}).get("album") or {}).get("release_date") or ""
                cache[u] = int(date[:4]) if date[:4].isdigit() else None
        except Exception as e:
            print(f"  batch {i//50}: {str(e)[:70]}")
            for u in batch:
                cache.setdefault(u, None)
        if (i // 50) % 25 == 0:
            json.dump(cache, open(CACHE, "w"))
            done = len(cache)
            print(f"  {done:,}/{len(uris):,}  ({100*done/len(uris):.0f}%)", flush=True)
        time.sleep(.06)

    json.dump(cache, open(CACHE, "w"))
    known = sum(1 for v in cache.values() if v)
    print(f"done: {known:,} of {len(cache):,} recordings have a release year")


if __name__ == "__main__":
    main()
