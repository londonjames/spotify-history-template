"""Cover art for podcast shows.

art.py works off Spotify ids, which the export carries for every track but not
for a show, so a show has to be found by name. One search each, 369 of them,
written into the same cache under "p" and keyed by the name the export uses.
"""
import json, os, sqlite3, sys, time
import requests
from art import Api, load, save, pick, DB

WANT = 300


def search(api, q, kind):
    if time.time() - api.got > 2700:
        from art import token
        api.tok, api.got = token(), time.time()
    for _ in range(6):
        r = requests.get("https://api.spotify.com/v1/search",
                         params={"q": q, "type": kind, "limit": 1, "market": "US"},
                         headers={"Authorization": f"Bearer {api.tok}"}, timeout=30)
        api.calls += 1
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 2)) + 1
            print(f"    rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        if r.status_code == 401:
            from art import token
            api.tok, api.got = token(), time.time()
            continue
        if r.status_code >= 500:
            time.sleep(2)
            continue
        r.raise_for_status()
        return r.json()
    return None


def main():
    cache = load()
    cache.setdefault("p", {})
    c = sqlite3.connect(DB)
    shows = [r[0] for r in c.execute(
        "SELECT s.name, COUNT(*) n FROM plays p JOIN shows s ON s.id = p.show_id"
        " WHERE s.name IS NOT NULL AND s.name <> ''"
        " GROUP BY s.id ORDER BY n DESC")]
    # A name that has already been looked up is never looked up again, including
    # one that came back with nothing, so a rerun costs only the new shows.
    todo = [s for s in shows if s not in cache["p"]]
    print(f"{len(shows)} shows, {len(todo)} to look up")
    api = Api()
    for i, name in enumerate(todo, 1):
        try:
            js = search(api, name, "show")
            items = (js or {}).get("shows", {}).get("items") or []
            hit = items[0] if items else None
            url = pick(hit.get("images"), WANT) if hit else None
            # Stored either way: "" is a show Spotify does not have, and
            # remembering that is what stops the next run asking again.
            cache["p"][name] = url or ""
        except Exception as e:
            print(f"    {name}: {e}")
            continue
        if i % 25 == 0:
            save(cache)
            print(f"  {i}/{len(todo)}  ({api.calls} requests)")
    save(cache)
    got = sum(1 for v in cache["p"].values() if v)
    print(f"done: {got} of {len(cache['p'])} shows have art, {api.calls} requests")


if __name__ == "__main__":
    main()
