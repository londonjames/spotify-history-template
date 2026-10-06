#!/usr/bin/env python3
"""Does the public need to see this?

Every podcast show and audiobook in the history has to carry an explicit
verdict before the site is published. Two files hold them:

    private.txt   hide  -- never appears on any page
    cleared.txt   keep  -- reviewed and fine to publish

A show in neither file is undecided, and this exits non-zero when any exist, so
`./refresh` stops rather than quietly publishing something nobody looked at.
That matters because a new export brings new shows, and the risk is always the
one that arrived since the last review.

Music is never reviewed. Every song and artist is published.

    python3 review.py            list undecided titles, newest listening first
    python3 review.py --all      show every verdict on record
    python3 review.py --hide "Show Name"
    python3 review.py --keep "Show Name"
    python3 review.py --keep-all   clear everything still undecided in one go
                                   (fine when the site only runs on your machine)
"""
import os
import sqlite3
import sys

import config

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "spotify.db")
PRIVATE = os.path.join(HERE, "private.txt")
CLEARED = os.path.join(HERE, "cleared.txt")

# Only a prompt for attention. Nothing is hidden on a keyword alone -- "The New
# Pornographers" is a rock band and "Heavyweight" is a storytelling show, so the
# words flag a show for a human look, they never decide anything.
FLAGS = [
    ("explicit", ["sex", "porn", "erotic", "masturbat", "naughty", "bdsm",
                  "kink", "nsfw", "shameless", "bellesa", "seduc"]),
    ("health", ["therapy", "mental", "anxiet", "depress", "addict", "recovery",
                "trauma", "grief", "fertility", "cancer", "diagnos"]),
    ("relationships", ["divorce", "affair", "marriage", "dating", "breakup"]),
    ("belief", ["god", "christ", "bible", "faith", "muslim", "jewish", "church"]),
    ("politics", ["trump", "biden", "maga", "left", "right wing", "election"]),
    ("money", ["salary", "net worth", "debt", "bankrupt"]),
]


def read_list(path):
    if not os.path.exists(path):
        return set(), []
    lines = [l.rstrip("\n") for l in open(path)]
    names = {l.split("#")[0].strip().lower() for l in lines if l.split("#")[0].strip()}
    return names, lines


def append(path, name, note):
    _, lines = read_list(path)
    if not lines:
        lines = [f"# {os.path.basename(path)}", ""]
    lines.append(f"{name}" + (f"  # {note}" if note else ""))
    open(path, "w").write("\n".join(lines) + "\n")


def flags_for(name):
    low = name.lower()
    return [tag for tag, words in FLAGS if any(w in low for w in words)]


def shows(who=config.NAME):
    """Every spoken-word title needing a verdict: podcast shows and audiobooks.

    Audiobooks are included because private.py checks them against the same list.
        """
    c = sqlite3.connect(DB)
    return c.execute(
        """SELECT show AS title, SUM(ms_played)/3600000.0 hrs, COUNT(*) n,
                  MAX(ts) last, 'podcast' AS kind
           FROM plays
           WHERE kind='podcast' AND who=? AND show IS NOT NULL
             AND (episode IS NULL OR episode <> show)
           GROUP BY show
           UNION ALL
           SELECT audiobook, SUM(ms_played)/3600000.0, COUNT(*),
                  MAX(ts), 'audiobook'
           FROM plays
           WHERE kind='audiobook' AND who=? AND audiobook IS NOT NULL
           GROUP BY audiobook
           ORDER BY last DESC""", (who, who)).fetchall()


def spotify_explicit(names, cache_path=os.path.join(HERE, ".explicit-cache.json")):
    """Look up Spotify's own explicit flag for each show name.

    Weak signal, kept only as one more hint. Spotify's explicit flag means
    "contains swearing", not "adult content": it marks 117 of these 356 shows,
    including The Rewatchables, Acquired, Huberman Lab and a children's story
    podcast. It is useful for noticing a show worth opening, never for deciding
    one. Results are cached so a re-run costs nothing.
    """
    import base64
    import json
    import requests
    from dotenv import load_dotenv

    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    todo = [n for n in names if n not in cache]
    if todo:
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.spotify"))
        auth = base64.b64encode(
            f'{os.getenv("SPOTIFY_CLIENT_ID")}:{os.getenv("SPOTIFY_CLIENT_SECRET")}'
            .encode()).decode()
        tok = requests.post("https://accounts.spotify.com/api/token",
                            headers={"Authorization": f"Basic {auth}"},
                            data={"grant_type": "client_credentials"}).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        for i, name in enumerate(todo, 1):
            try:
                r = requests.get("https://api.spotify.com/v1/search", headers=H,
                                 params={"q": name, "type": "show",
                                         "market": "US", "limit": 1}, timeout=15).json()
                items = r.get("shows", {}).get("items") or []
                # Only trust an exact title match; search is fuzzy and will
                # happily return a different show for an obscure name.
                hit = items[0] if items and items[0]["name"].strip().lower() == name.strip().lower() else None
                cache[name] = {"explicit": bool(hit and hit.get("explicit")),
                               "matched": bool(hit)}
            except Exception as e:
                cache[name] = {"explicit": False, "matched": False, "error": str(e)[:60]}
            if i % 40 == 0:
                print(f"    ...{i}/{len(todo)}", flush=True)
        json.dump(cache, open(cache_path, "w"), indent=0)
    return cache


def main(argv):
    hidden, _ = read_list(PRIVATE)
    kept, _ = read_list(CLEARED)

    if len(argv) >= 2 and argv[0] in ("--hide", "--keep"):
        name = argv[1]
        note = argv[2] if len(argv) > 2 else ""
        append(PRIVATE if argv[0] == "--hide" else CLEARED, name, note)
        print(f"{'hidden' if argv[0]=='--hide' else 'cleared'}: {name}")
        return 0

    rows = shows()
    show_all = "--all" in argv

    if "--scan" in argv:
        print(f"Checking Spotify's explicit flag for {len(rows)} shows...")
        cache = spotify_explicit([r[0] for r in rows])
        flagged = [r for r in rows if cache.get(r[0], {}).get("explicit")]
        unmatched = sum(1 for r in rows if not cache.get(r[0], {}).get("matched"))
        print(f"\n{len(flagged)} marked explicit by Spotify "
              f"({unmatched} shows could not be matched and need a human look):\n")
        for name, hrs, n, last, _kind in sorted(flagged, key=lambda r: -r[1]):
            state = "already hidden" if name.strip().lower() in hidden else "NOT HIDDEN"
            print(f"  {state:<15} {hrs:6.2f}h  {name[:60]}")
        return 0
    undecided = [r for r in rows if r[0].strip().lower() not in hidden | kept]
    if "--keep-all" in argv:
        for r in undecided:
            append(CLEARED, r[0], "")
        print(f"cleared {len(undecided)} titles")
        return 0

    if show_all:
        for name, hrs, n, last, kind in rows:
            state = ("HIDE" if name.strip().lower() in hidden
                     else "keep" if name.strip().lower() in kept else "?")
            tag = "book" if kind == "audiobook" else "show"
            print(f"  {state:<5} {tag}  {hrs:6.1f}h  {name[:60]}")
        print()

    print(f"{len(rows)} spoken-word titles: {len(hidden & {r[0].strip().lower() for r in rows})} hidden, "
          f"{len(kept & {r[0].strip().lower() for r in rows})} cleared, {len(undecided)} undecided")

    if not undecided:
        print("Every show has a verdict. Safe to publish.")
        return 0

    print("\nUndecided (most recently played first):\n")
    for name, hrs, n, last, kind in undecided:
        tags = flags_for(name)
        mark = ("  [" + ", ".join(tags) + "]") if tags else ""
        tag = "book" if kind == "audiobook" else "show"
        print(f"  {tag}  {hrs:6.1f}h  {n:>4} plays  {last[:10]}  {name[:60]}{mark}")
    print(f"\nDecide each with:  python3 review.py --hide \"Name\"  |  --keep \"Name\"")
    print("Or clear all of them at once:  python3 review.py --keep-all")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
