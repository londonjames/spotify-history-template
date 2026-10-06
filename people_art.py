#!/usr/bin/env python3
"""Fetch photographs of podcast people into art_cache.json.

Spotify has no picture of a podcast guest. It has artists, shows and episodes,
and a name search for "Chris Ryan" returns a musician. So a face comes from,
in order:

  1. people_photos.txt, by hand -- for when the sources below are wrong or
     have nothing. "Name | url", or "Name | none" to refuse a bad match.
  2. The Ringer's creator page, for anyone heard on a Ringer show. Most of the
     top of the list are Ringer staff, and its headshots are theirs.
  3. Wikipedia, found by searching the name together with the show they were
     on (or "podcast"), and only taken when the page title is that name and
     its description reads like a person. A bare name lookup put the SAS
     author on the Rewatchables' Chris Ryan.

Anyone left without a face gets their show's cover on the page.

The cache is keyed on the name as the page shows it and never invalidated. A
miss is stored as null so it is not asked again.

    python3 people_art.py            fill in whatever is missing
    python3 people_art.py --retry    ask again about the misses
"""
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "art_cache.json")
INDEX = os.path.join(HERE, "web", "index.json")
MANUAL = os.path.join(HERE, "people_photos.txt")
WIKI = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "spotify-history/1.0 (personal listening archive)"}

RINGER_SHOWS = {"The Bill Simmons Podcast", "The Rewatchables", "The Big Picture",
                "Book of Basketball 2.0", "The Watch", "HBO's Succession Podcast"}

PERSON = re.compile(
    r"\b(born|b\.|actor|actress|writer|journalist|podcaster|host|presenter|"
    r"director|producer|comedian|player|coach|athlete|executive|entrepreneur|"
    r"investor|author|columnist|commentator|broadcaster|analyst|personality|"
    r"musician|singer|rapper|songwriter|scientist|professor|economist|"
    r"politician|businessman|businesswoman|editor|critic|screenwriter|"
    r"filmmaker|reporter|anchor|swimmer|runner|tennis|basketball|football|"
    r"baseball|neuroscientist|physician|psychologist|chef|designer|founder|"
    r"venture capitalist|ultramarathon|triathlete|cyclist|historian)\b", re.I)


def clean(name):
    """The title parser leaves trailing punctuation and honorifics on."""
    n = re.sub(r"[!?.,:;]+$", "", name.strip())
    return re.sub(r"^(Dr|Mr|Mrs|Ms|Prof)\.?\s+", "", n)


def manual():
    out = {}
    if os.path.exists(MANUAL):
        for line in open(MANUAL):
            line = line.split("#")[0].strip()
            if "|" in line:
                n, u = (x.strip() for x in line.split("|", 1))
                out[n] = None if u.lower() == "none" else u
    return out


def ringer(name):
    slug = re.sub(r"[^a-z0-9]+", "-", clean(name).lower()).strip("-")
    try:
        r = requests.get(f"https://www.theringer.com/creator/{slug}", timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (Macintosh)"})
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    m = re.search(r'https://wp\.theringer\.com/wp-content/uploads/[^"\s]*headshot[^"\s]*?'
                  r'\.(?:jpe?g|png|webp)', r.text)
    return m.group(0) if m else None


def wiki(name, show):
    n = clean(name)
    r = requests.get(WIKI, headers=UA, timeout=30, params={
        "action": "query", "format": "json", "list": "search", "srlimit": 10,
        "srsearch": f'"{n}" ("{show}" OR podcast)'})
    time.sleep(0.3)
    if r.status_code != 200:
        return None
    titles = [x["title"] for x in r.json()["query"]["search"]]
    # The plain title first; a qualified one ("Matt Walker (drummer)") only
    # when it is the sole candidate, since several mean the name is common.
    exact = [t for t in titles if t.lower() == n.lower()]
    qual = [t for t in titles if re.sub(r"\s*\(.*\)$", "", t).lower() == n.lower()]
    pick = exact[:1] or (qual if len(qual) == 1 else [])
    if not pick:
        return None
    p = requests.get(WIKI, headers=UA, timeout=30, params={
        "action": "query", "format": "json", "formatversion": 2, "titles": pick[0],
        "prop": "pageimages|description", "piprop": "thumbnail",
        "pithumbsize": 240}).json()["query"]["pages"][0]
    if not PERSON.search(p.get("description") or ""):
        return None
    return (p.get("thumbnail") or {}).get("source")


def main():
    cache = json.load(open(CACHE))
    g = cache.setdefault("g", {})
    d = json.load(open(INDEX))
    graph = d["graph"]
    hand = manual()
    retry = "--retry" in sys.argv
    names = list(graph)
    todo = [n for n in names if n not in hand
            and (n not in g or (retry and g[n] is None))]
    print(f"{len(names)} people, {len(todo)} to ask, {len(hand)} by hand")
    for i, n in enumerate(todo, 1):
        shows = [s for s, _, _ in graph[n]]
        url = ringer(n) if RINGER_SHOWS & set(shows) else None
        g[n] = url or wiki(n, shows[0])
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}")
    g.update(hand)
    tmp = CACHE + ".tmp"
    json.dump(cache, open(tmp, "w"), separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, CACHE)
    print(f"  {sum(1 for n in names if g.get(n))} of {len(names)} have a photograph")


if __name__ == "__main__":
    main()
