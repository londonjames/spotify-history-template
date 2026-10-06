#!/usr/bin/env python3
"""Export a compact search index from spotify.db for the local web UI.

One row per searchable thing (track / artist / podcast). Year counts are stored
sparsely as "yearIndex:count" pairs because most tracks only live in a year or
two, which keeps the whole 15-year history small enough to load in one go.

The year range is read from the data, so when a future export extends the
history into 2027 the UI picks that up with no code change.
"""
import importlib.util

import config
import json
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "spotify.db")
OUT = os.path.join(HERE, "web", "index.json")

# Reuse the loader's edition rules so display names and grouping never disagree.
_spec = importlib.util.spec_from_file_location("_load", os.path.join(HERE, "load.py"))
_load = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_load)
strip_edition = _load.strip_edition
norm_title, norm_artist = _load.norm_title, _load.norm_artist
KIDS_FILE = os.path.join(HERE, "kids.txt")

_pspec = importlib.util.spec_from_file_location("_private", os.path.join(HERE, "private.py"))
_private = importlib.util.module_from_spec(_pspec)
_pspec.loader.exec_module(_private)

_gspec = importlib.util.spec_from_file_location("_guests", os.path.join(HERE, "guests.py"))
_guests = importlib.util.module_from_spec(_gspec)
_gspec.loader.exec_module(_guests)


def load_kids():
    """Read kids.txt into (names, {(artist, track)}, [album prefixes])."""
    names, tracks, albums = set(), set(), []
    if not os.path.exists(KIDS_FILE):
        return names, tracks, albums
    for line in open(KIDS_FILE):
        line = line.split("#")[0].strip()
        if not line:
            continue
        if line.startswith("~"):
            albums.append(line[1:].strip().lower())
        elif "::" in line:
            a, t = line.split("::", 1)
            tracks.add((norm_artist(a.strip()), norm_title(t.strip())))
        else:
            names.add(norm_artist(line))
    return names, tracks, albums


def main(who=config.NAME):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    # Registered as a SQL function so the exclusion lives in the queries, and a
    # hidden show cannot leak into a total that forgot to filter.
    blocked = _private.make_filter()
    c.create_function("is_private", 1, lambda s: 1 if blocked(s) else 0)
    # Hidden shows never appear in a list; their hours still count in the totals.
    # episode = show catches Spotify's 2018 music videos, filed as podcasts.
    PRIV = " AND NOT is_private(show) AND (episode IS NULL OR episode <> show)"
    y0, y1 = c.execute(
        "SELECT MIN(year), MAX(year) FROM plays WHERE who=?", (who,)).fetchone()
    kid_names, kid_tracks, kid_albums = load_kids()

    # Resolve every recording once. An album prefix catches a whole soundtrack
    # without tagging its credited artists -- Halsey sang on Sing 2, but Halsey
    # is not the kids.
    kid_keys = set()
    for tn, an, album in c.execute(
            """SELECT DISTINCT track_norm, artist_norm, album FROM plays
               WHERE kind='track' AND who=? AND track_norm IS NOT NULL""", (who,)):
        al = (album or "").lower()
        if (an in kid_names or (an, tn) in kid_tracks
                or any(al.startswith(p) for p in kid_albums)):
            kid_keys.add((tn, an))

    rows = []

    def is_kids(artist_norm, track_norm=None):
        if artist_norm in kid_names:
            return True
        return track_norm is not None and (track_norm, artist_norm) in kid_keys

    def collect(sql, kind):
        agg = {}
        for r in c.execute(sql, (who,)):
            e = agg.setdefault(r["k"], {
                "n": r["name"], "a": r["sub"] or "", "y": {}, "p": 0, "m": 0,
                "f": r["f"], "l": r["l"], "sk": 0, "sh": 0})
            e["y"][r["year"]] = r["n_plays"]
            e["p"] += r["n_plays"]
            e["m"] += r["ms"]
            e["sk"] += r["sk"] or 0
            e["sh"] += r["sh"] or 0
            e["f"] = min(e["f"], r["f"])
            e["l"] = max(e["l"], r["l"])
        for k, e in agg.items():
            # Track keys are "track_norm \x1f artist_norm"; artist keys are the
            # artist alone. Podcasts are never tagged.
            if kind == "t":
                tn, an = k.split(chr(31))
                kid = is_kids(an, tn)
            elif kind == "a":
                kid = is_kids(k)
            else:
                # A bare line in kids.txt matches a podcast name too, so a kids'
                # show can be tagged the same way as a kids' artist.
                kid = is_kids(norm_artist(e["n"]))
            rows.append([
                kind, best.get(k, e["n"]), e["a"], e["p"], round(e["m"] / 60000),
                "|".join(f"{y - y0}:{n}" for y, n in sorted(e["y"].items()) if n),
                e["f"][:10], e["l"][:10],
                round(100 * e["sk"] / e["p"]) if e["p"] else 0,
                round(100 * e["sh"] / e["p"]) if e["p"] else 0,
                1 if kid else 0,
            ])

    # Show the edition you actually played most, so a merged group reads as
    # "Whatever" rather than whichever variant happened to sort first.
    best = {}
    for k, norm, name, n in c.execute(
            """SELECT track_norm || CHAR(31) || artist_norm k, track_norm, track, COUNT(*) n
               FROM plays WHERE kind='track' AND who=? AND track_norm IS NOT NULL
               GROUP BY track_norm, artist_norm, track ORDER BY n""", (who,)):
        # Later rows are more played, so they win -- except that a title already
        # in its clean form beats any edition label, however often it was played.
        if k not in best or name.lower() == norm or best[k].lower() != norm:
            best[k] = name
    # Where every edition carried a label (you only ever played the remaster),
    # strip it for display so it reads "In My Life", not "In My Life - Remastered 2009".
    best = {k: (v if v.lower() == k.split(chr(31))[0] else strip_edition(v))
            for k, v in best.items()}

    collect("""SELECT track_norm || CHAR(31) || artist_norm k,
                      MIN(track) name, MIN(artist) sub, year,
                      COUNT(*) n_plays, SUM(ms_played) ms, MIN(ts) f, MAX(ts) l,
                      SUM(skipped) sk, SUM(shuffle) sh
               FROM plays WHERE kind='track' AND who=? AND track_norm IS NOT NULL
               GROUP BY track_norm, artist_norm, year""", "t")

    collect("""SELECT artist_norm k, MIN(artist) name, '' sub, year,
                      COUNT(*) n_plays, SUM(ms_played) ms, MIN(ts) f, MAX(ts) l,
                      SUM(skipped) sk, SUM(shuffle) sh
               FROM plays WHERE kind='track' AND who=? AND artist_norm IS NOT NULL
               GROUP BY artist_norm, year""", "a")

    collect("""SELECT LOWER(show) k, MIN(show) name, '' sub, year,
                      COUNT(*) n_plays, SUM(ms_played) ms, MIN(ts) f, MAX(ts) l,
                      0 sk, 0 sh
               FROM plays WHERE kind='podcast' AND who=? AND show IS NOT NULL"""
            + PRIV + """ GROUP BY LOWER(show), year""", "p")

    # People. Spotify records no guest, so names come out of episode titles;
    # see guests.py. Someone on a large share of a show's episodes is running it,
    # not visiting, and that split is what makes a "favourite guests" list useful
    # -- otherwise the Rewatchables hosts swamp everyone who actually appeared.
    ep_rows = c.execute(
        """SELECT show, episode, year, ms_played, ts FROM plays
           WHERE kind='podcast' AND who=? AND show IS NOT NULL AND episode IS NOT NULL"""
        + PRIV, (who,)).fetchall()
    bare = _guests.bare_name_shows([(r["show"], r["episode"]) for r in ep_rows])

    show_eps = {}
    for r in ep_rows:
        show_eps.setdefault(r["show"], set()).add(r["episode"])

    people = {}
    for r in ep_rows:
        for name in _guests.extract(r["episode"], r["show"] in bare):
            e = people.setdefault(name.lower(), {
                "n": name, "y": {}, "p": 0, "m": 0,
                "f": r["ts"], "l": r["ts"], "shows": {}})
            e["f"] = min(e["f"], r["ts"])
            e["l"] = max(e["l"], r["ts"])
            e["y"][r["year"]] = e["y"].get(r["year"], 0) + 1
            e["p"] += 1
            e["m"] += r["ms_played"]
            e["shows"].setdefault(r["show"], set()).add(r["episode"])

    for key, e in people.items():
        # Regular on a show if they appear on a quarter of the episodes heard.
        roles = []
        for show, eps in e["shows"].items():
            share = len(eps) / max(1, len(show_eps[show]))
            roles.append(("regular" if (share >= 0.25 and len(eps) >= 5)
                          else "guest", show, len(eps)))
        regular = [r for r in roles if r[0] == "regular"]
        top = max(roles, key=lambda r: r[2])
        sub = ("Regular · " + regular[0][1]) if regular else ("Guest · " + top[1])
        if len(e["shows"]) > 1:
            sub += f" +{len(e['shows']) - 1}"
        rows.append([
            "g", e["n"], sub, e["p"], round(e["m"] / 60000),
            "|".join(f"{y - y0}:{n}" for y, n in sorted(e["y"].items()) if n),
            e["f"][:10], e["l"][:10], 0, 0, 0,
        ])

    rows.sort(key=lambda r: -r[3])

    s = c.execute("""SELECT COUNT(*) n, SUM(ms_played) ms, MIN(ts) f, MAX(ts) l
                     FROM plays WHERE who=?""", (who,)).fetchone()
    years = range(y0, y1 + 1)
    byyear = {r[0]: (r[1], r[2]) for r in c.execute(
        "SELECT year, SUM(ms_played), COUNT(*) FROM plays WHERE who=? GROUP BY year",
        (who,))}
    kinds = {k: sum(1 for r in rows if r[0] == k) for k in "tapg"}

    # The kids' share per year, so hiding it gives exact totals rather than an
    # estimate. Measured from the plays table, not from the aggregated rows.
    kid_year = {y: [0, 0] for y in years}
    kid_tot = [0, 0]
    for year, ms, n, an, tn in c.execute(
            """SELECT year, SUM(ms_played), COUNT(*), artist_norm, track_norm
               FROM plays WHERE kind='track' AND who=? AND artist_norm IS NOT NULL
               GROUP BY year, artist_norm, track_norm""", (who,)):
        if is_kids(an, tn):
            kid_year[year][0] += ms
            kid_year[year][1] += n
            kid_tot[0] += ms
            kid_tot[1] += n
    # Kids' podcasts count too, or the totals would disagree with the lists that
    # already hide them.
    for year, ms, n, show in c.execute(
            """SELECT year, SUM(ms_played), COUNT(*), show FROM plays
               WHERE kind='podcast' AND who=? AND show IS NOT NULL
               GROUP BY year, show""", (who,)):
        if norm_artist(show) in kid_names:
            kid_year[year][0] += ms
            kid_year[year][1] += n
            kid_tot[0] += ms
            kid_tot[1] += n

    payload = {
        "y0": y0, "y1": y1, "who": who,
        "stats": {
            "plays": s["n"], "hours": round(s["ms"] / 3.6e6),
            "first": s["f"][:10], "last": s["l"][:10],
            "tracks": kinds["t"], "artists": kinds["a"], "shows": kinds["p"],
            "people": kinds["g"],
            "hoursByYear": [round(byyear.get(y, (0, 0))[0] / 3.6e6) for y in years],
            "playsByYear": [byyear.get(y, (0, 0))[1] for y in years],
            "mineHoursByYear": [round((byyear.get(y, (0, 0))[0] - kid_year[y][0]) / 3.6e6)
                                for y in years],
            "minePlaysByYear": [byyear.get(y, (0, 0))[1] - kid_year[y][1] for y in years],
            "minePlays": s["n"] - kid_tot[1],
            "mineHours": round((s["ms"] - kid_tot[0]) / 3.6e6),
        },
        "cols": ["kind", "name", "artist", "plays", "minutes", "years",
                 "first", "last", "skipPct", "shufflePct", "kids"],
        "rows": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)
    print(f"{len(rows):,} entities -> web/index.json "
          f"({os.path.getsize(OUT)/1e6:.1f} MB), {y0}-{y1}")
    print(f"  {kinds['t']:,} tracks · {kinds['a']:,} artists · "
          f"{kinds['p']:,} podcasts · {kinds['g']:,} people")


if __name__ == "__main__":
    main()
