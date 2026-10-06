#!/usr/bin/env python3
"""Fetch cover art and artist photographs from Spotify into art_cache.json.

The page had nothing but type on it. A record is a picture as much as a title,
and a list of forty artist names is a list; the same list with their faces on it
is a shelf.

Two things make this cheap. The export already stores a Spotify id for every
one of the 45,855 recordings, so nothing has to be searched for by name and
guessed at. And the API is asked once, here, at build time -- never by a
visitor -- so the page carries urls rather than a key, and an image costs a
request no matter how many people look at it.

The cache is the point. It is keyed on things that do not change (a track's id,
an artist's name, an album and its artist) and is never invalidated, so a
rebuild refetches nothing and a half-finished run picks up where it stopped.

    python3 art.py              fill in whatever is missing
    python3 art.py --all        include tracks played once or twice
    python3 art.py --limit 500  stop after 500 requests, to try it out
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
DB = os.path.join(HERE, "web", "listening.db")
CACHE = os.path.join(HERE, "art_cache.json")
ENV = os.path.join(HERE, ".env.spotify")

# A track needs this many plays to be worth a picture, matching the floor
# web_data.py uses to decide what is inlined into the page at all.
MIN_PLAYS = 3
BATCH = 50


def token():
    load_dotenv(ENV)
    cid, sec = os.getenv("SPOTIFY_CLIENT_ID"), os.getenv("SPOTIFY_CLIENT_SECRET")
    if not (cid and sec):
        sys.exit(f"No SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET in {ENV}")
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    r = requests.post("https://accounts.spotify.com/api/token",
                      headers={"Authorization": f"Basic {auth}"},
                      data={"grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


class Api:
    """Client credentials, renewed before they lapse, with 429 respected."""

    def __init__(self):
        self.tok = token()
        self.got = time.time()
        self.calls = 0

    def get(self, path, ids):
        if time.time() - self.got > 2700:      # the token lasts an hour
            self.tok, self.got = token(), time.time()
        for attempt in range(6):
            r = requests.get(f"https://api.spotify.com/v1/{path}",
                             params={"ids": ",".join(ids)},
                             headers={"Authorization": f"Bearer {self.tok}"},
                             timeout=30)
            self.calls += 1
            if r.status_code == 429:
                # Spotify says how long to wait; waiting is cheaper than failing
                # two hundred requests into a run.
                wait = int(r.headers.get("Retry-After", 2)) + 1
                print(f"    rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            if r.status_code == 401:
                self.tok, self.got = token(), time.time()
                continue
            r.raise_for_status()
            return r.json()
        raise SystemExit("gave up after six attempts on " + path)


def pick(images, want):
    """The smallest image at least `want` wide, or the largest there is.

    Spotify returns 640/300/64 for a cover and 1000/640/200/64 for a face. A
    thumbnail wants the small one -- shipping 640px into a 44px square is a
    megabyte of nothing.
    """
    if not images:
        return None
    big = [i for i in images if i.get("width") and i["width"] >= want]
    return (min(big, key=lambda i: i["width"]) if big
            else max(images, key=lambda i: i.get("width") or 0))["url"]


def load():
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    return {"t": {}, "a": {}, "l": {}}


def save(cache):
    tmp = CACHE + ".tmp"
    json.dump(cache, open(tmp, "w"), separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, CACHE)


def main():
    every = "--all" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    cache = load()
    before = {k: len(v) for k, v in cache.items()}
    c = sqlite3.connect(DB)

    floor = 1 if every else MIN_PLAYS
    rows = c.execute(f"""
        SELECT t.uri, t.name, ifnull(a.name, ''), t.album
          FROM plays p JOIN tracks t ON t.id = p.track_id
          LEFT JOIN artists a ON a.id = t.artist_id
         WHERE p.is_kid = 0 AND p.is_ambient = 0 AND t.uri IS NOT NULL
         GROUP BY t.id HAVING COUNT(*) >= {floor}""").fetchall()
    # The song "On this day" shows for each date is often one played once,
    # under the floor above, and a card that changes daily cannot have gaps.
    import otd
    have = {r[0] for r in rows}
    rows += [(uri, nm, ar, al) for _, _, nm, ar, al, uri, _, _ in otd.picks(c, prefer_pic=False)
             if uri and uri not in have and not have.add(uri)]
    c.close()

    want = [(uri.split(":")[-1], nm, ar, al) for uri, nm, ar, al in rows]
    todo = [w for w in want if w[0] not in cache["t"]]
    print(f"{len(want):,} tracks at {floor}+ plays, {len(todo):,} without art")

    api = Api()
    artists = {}          # id -> name, gathered on the way past

    for i in range(0, len(todo), BATCH):
        if limit and api.calls >= limit:
            print("  stopping at the request limit"); break
        chunk = todo[i:i + BATCH]
        data = api.get("tracks", [t[0] for t in chunk])
        for (tid, nm, ar, al), t in zip(chunk, data.get("tracks") or []):
            if not t:
                # A track can be pulled from the catalogue after he played it.
                cache["t"][tid] = None
                continue
            cover = pick((t.get("album") or {}).get("images") or [], 300)
            cache["t"][tid] = cover
            if cover and al:
                cache["l"].setdefault(f"{al}\x00{ar}", cover)
            for a in t.get("artists") or []:
                if a.get("id") and a.get("name"):
                    artists[a["id"]] = a["name"]
        if i // BATCH % 10 == 0:
            save(cache)
            print(f"  {min(i + BATCH, len(todo)):,}/{len(todo):,} tracks")
    save(cache)

    missing = {aid: nm for aid, nm in artists.items() if nm not in cache["a"]}
    print(f"{len(artists):,} artists seen, {len(missing):,} without a photograph")
    ids = list(missing)
    for i in range(0, len(ids), BATCH):
        if limit and api.calls >= limit:
            print("  stopping at the request limit"); break
        chunk = ids[i:i + BATCH]
        data = api.get("artists", chunk)
        for aid, a in zip(chunk, data.get("artists") or []):
            if a:
                cache["a"][a["name"]] = pick(a.get("images") or [], 200)
        if i // BATCH % 10 == 0:
            save(cache)
            print(f"  {min(i + BATCH, len(ids)):,}/{len(ids):,} artists")
    save(cache)

    after = {k: len(v) for k, v in cache.items()}
    print(f"\nart_cache.json  {os.path.getsize(CACHE)/1e6:.1f} MB  "
          f"{api.calls} requests")
    for k, label in (("t", "tracks"), ("l", "albums"), ("a", "artists")):
        print(f"  {label:8} {after[k]:>6,}  (+{after[k] - before[k]:,})")
    print("\npublish_db.py writes these into listening.db on the next build.")


if __name__ == "__main__":
    main()
