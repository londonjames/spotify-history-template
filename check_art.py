#!/usr/bin/env python3
"""Stop the build when a row at the top of a list would show an empty square.

Reads what the page reads -- the rows in web/index.json and the art table in
web/listening.db -- and looks up a picture for each row the same way app.html
does (artFor). Checked: the first TOP rows of artists, albums, songs and
podcasts over all time, the first YEAR_TOP of each in every single year, and
the song "On this day" shows for every date in the history.
People are left out, because one without a face is drawn with their initials.

    python3 check_art.py          exit 1 and list the rows with no picture
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
TOP, YEAR_TOP = 20, 10
KINDS = {"a": "artist", "l": "album", "t": "song", "p": "podcast"}
SEP = "\u001f"


def main():
    d = json.load(open(os.path.join(WEB, "index.json")))
    c = {n: i for i, n in enumerate(d["cols"])}
    art = {k: {} for k in "tlapg"}
    db = sqlite3.connect(os.path.join(WEB, "listening.db"))
    for kind, key, url in db.execute("SELECT kind, key, url FROM art"):
        if kind in art and url:
            art[kind][key] = url
    # An artist with no photograph borrows one of their own album covers.
    albumof = {}
    for key, url in art["l"].items():
        who = key.split(SEP)[1] if SEP in key else ""
        if who:
            albumof.setdefault(who, url)

    def pic(r):
        k, name, sub = r[c["kind"]], r[c["name"]], r[c["sub"]] or ""
        if k == "a":
            return art["a"].get(name) or albumof.get(name)
        if k == "p":
            return art["p"].get(name)
        return art[k].get(name + SEP + sub)

    # His own listening, which is what the page opens on.
    def mine(r):
        return r[c["plays"]] - r[c["kidPlays"]] - r[c["ambPlays"]]

    def years(r):
        return {int(y): int(n) for y, n in
                (p.split(":") for p in (r[c["years"]] or "").split("|") if p)}

    missing = {}
    for k, label in KINDS.items():
        rows = [r for r in d["rows"] if r[c["kind"]] == k and mine(r) > 0]
        rows.sort(key=mine, reverse=True)
        for r in rows[:TOP]:
            if not pic(r):
                missing.setdefault((label, r[c["name"]], r[c["sub"]]), []).append("all time")
        for yi, year in enumerate(d["years"]):
            def val(r):
                return years(r).get(yi, 0) * mine(r) / max(1, r[c["plays"]])
            ranked = sorted((r for r in rows if val(r) > 0), key=val, reverse=True)
            for r in ranked[:YEAR_TOP]:
                if not pic(r):
                    missing.setdefault((label, r[c["name"]], r[c["sub"]]), []).append(str(year))

    # "On this day" shows one song for today's date in each year, so every
    # date of the year has to be whole before any of them comes round.
    import otd
    for md, year, name, artist, album, uri, n, has in otd.picks(db):
        if not has:
            missing.setdefault(("on this day", name, artist), []).append(f"{md} {year}")

    if not missing:
        print(f"  every top row has a picture (top {TOP} all time, top {YEAR_TOP} each year)")
        return 0
    print(f"  {len(missing)} top rows have no picture:")
    for (label, name, sub), where in sorted(missing.items()):
        print(f"    {label}: {name}{' — ' + sub if sub else ''}  ({', '.join(where)})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
