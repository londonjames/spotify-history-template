#!/usr/bin/env python3
"""Rebuild web/phases.json -- the measured half of the story page.

The page is written in two halves. story_copy.json says what a stretch of the
history felt like, which only a person can do. This file says what it was: the
same twelve windows, every figure counted out of web/listening.db. story_data.py
then refuses to build if the prose quotes a number the data no longer supports.
(A bonus scene about one artist can be written straight into story_copy.json;
it has no measured window, so it does not appear here.)

Nothing here chooses where a season starts. The boundaries were scored once and
then argued with -- four of the twelve are drawn by judgement rather than by the
change score, and story_copy.json is written against those dates -- so they are
input, held in SEASONS below with the labels and the hand-picked signature
track. Rerunning this recomputes the figures inside those windows and leaves the
editing alone. If a boundary really should move, move it here, and expect to
rewrite the copy that describes it.

    python3 phases.py

The window is inclusive of both months, the children's listening and the
overnight speaker are out of every figure except `away`, and the file it writes
is the one story_data.py reads.
"""
import json
import os
import sqlite3
import statistics
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "web", "listening.db")
OUT = os.path.join(HERE, "web", "phases.json")

HOUR = 3.6e6

# What counts as the owner listening, rather than what played on the account. Both flags
# are decided once, in publish_db.py -- the children's tags from kids.txt, the
# overnight speaker from the device and the hour -- so this file inherits those
# rules rather than keeping a second opinion about them.
LIVE = "is_kid = 0 AND is_ambient = 0"

# The small hours are before six in the morning. A play at 05:40 belongs to the
# night it started, not to the morning it lands in.
SMALL_HOURS = 6

# Where the records came out, in the bands the page's strip draws (the DEC
# labels in web/story.html): decades until 2000, then five-year bands. The later
# years are cut finer because the move from 2005 records to 2015 records is most
# of what changes across these seasons, and a decade-wide band would hide it.
BANDS = [1970, 1980, 1990, 2000, 2005, 2010, 2015, 2020]

# A country needs either a second day or twenty plays to count as having been
# somewhere. Below that it is a layover with the phone on, and the trip it
# implies did not happen.
AWAY_DAYS, AWAY_PLAYS = 2, 20

# An artist is "defining" if this window holds most of everything he has ever
# played of them. The three floors keep out the arithmetic of small numbers: at
# twenty plays and an hour across the whole history, and half an hour inside the
# window, a share of 100% still means something was actually listened to.
# Without them every season fills with names heard once and never again.
DEFINING_PLAYS, DEFINING_LIFE, DEFINING_HERE = 20, HOUR, 0.5 * HOUR

# The editorial half: the dates, the names and the chosen track. Everything
# else in phases.json is counted from these.
SEASONS_FILE = os.path.join(HERE, "seasons.json")


def read_seasons():
    """The windows, from seasons.json: a list of
         {"id": "S1", "label": "...", "from": "2015-01", "to": "2016-06",
          "signature": {"track": "...", "artist": "...", "uri": "spotify:track:..."}}
    Only id, from and to are required. Without a signature the season plays its
    most-repeated track."""
    out = []
    for p in json.load(open(SEASONS_FILE)):
        out.append({"label": p["id"], "boundary": "strong", "boundary_note": "",
                    "signature": None, "kids_note": "", "notes": "", **p})
    return out


SEASONS = read_seasons() if os.path.exists(SEASONS_FILE) else []


def months_between(a, b):
    """Calendar months in the window, both ends included."""
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7]) + 1


def band(year):
    return sum(1 for cut in BANDS if year >= cut)


def shares(counts, total):
    """Percentages that add up to 100.

    Rounding each band on its own leaves the strip summing to 99 or 101, and the
    strip is read as a whole. The point or two is settled on the tallest band,
    where it is a rounding difference rather than a visible change.
    """
    exact = [100 * c / total for c in counts]
    out = [round(x) for x in exact]
    out[exact.index(max(exact))] += 100 - sum(out)
    return out


def load(db):
    """Every play, with the names joined on and the two filters resolved."""
    return db.execute(f"""
        SELECT p.day, p.hour, p.ms, p.kind, p.country, p.device, p.release_year,
               a.name, t.name, t.uri, CASE WHEN {LIVE} THEN 1 ELSE 0 END
        FROM plays p
        LEFT JOIN artists a ON a.id = p.artist_id
        LEFT JOIN tracks  t ON t.id = p.track_id
        ORDER BY p.day""").fetchall()


DAY, HR, MS, KIND, CC, DEV, YEAR, ARTIST, TRACK, URI, IS_LIVE = range(11)


def figures(rows, all_rows, p, first_heard, lifetime):
    """Everything about one season that the database can answer."""
    music = [r for r in rows if r[KIND] == "track" and r[ARTIST]]
    pods = [r for r in rows if r[KIND] == "podcast"]
    months = months_between(p["from"], p["to"])

    plays = Counter(r[ARTIST] for r in music)
    top = plays.most_common(8)
    # Ties are settled on the title rather than on whichever row the database
    # happens to hold first, so a rebuild returns the same track.
    tracks = Counter((r[TRACK], r[ARTIST]) for r in music if r[TRACK])
    (t_name, t_artist), t_plays = max(tracks.items(), key=lambda kv: (kv[1], kv[0]))
    uri = next((r[URI] for r in music if (r[TRACK], r[ARTIST]) == (t_name, t_artist)
                and r[URI]), None)

    # The share of plays that went to an artist first heard inside this window.
    # Counting names instead of plays would make every season read as discovery,
    # because most of the names in any stretch are heard once and forgotten.
    new = sum(1 for r in music if first_heard[r[ARTIST]] >= p["from"])

    years = [r[YEAR] for r in music if r[YEAR]]
    bands = Counter(band(y) for y in years)

    devices = Counter(r[DEV] for r in rows)
    device, device_n = devices.most_common(1)[0]

    # Where he was is a fact about the household, not about his own listening,
    # so `away` is the only figure counted over every row -- the children's
    # plays abroad are still the family being abroad.
    trips = Counter()
    days, seen = defaultdict(set), {}
    for r in all_rows:
        if r[CC] and r[CC] != "US":
            trips[r[CC]] += 1
            days[r[CC]].add(r[DAY])
            seen.setdefault(r[CC], [r[DAY], r[DAY]])[1] = r[DAY]
    away = [[cc, n, len(days[cc]), seen[cc][0], seen[cc][1]]
            for cc, n in trips.most_common()
            if len(days[cc]) >= AWAY_DAYS or n >= AWAY_PLAYS]

    here = defaultdict(int)
    for r in music:
        here[r[ARTIST]] += r[MS]
    defining = sorted(((a, ms, ms / lifetime[a][0]) for a, ms in here.items()
                       if lifetime[a][1] >= DEFINING_PLAYS
                       and lifetime[a][0] >= DEFINING_LIFE
                       and ms >= DEFINING_HERE),
                      key=lambda t: (-t[2], -t[1]))[:3]

    return {
        "id": p["id"],
        "label": p["label"],
        "from": p["from"],
        "to": p["to"],
        "boundary": p["boundary"],
        "boundary_note": p["boundary_note"],
        "months": months,
        "plays": len(rows),
        "hours": round(sum(r[MS] for r in rows) / HOUR),
        "plays_per_mo": round(len(rows) / months),
        "music_h_per_mo": round(sum(r[MS] for r in music) / HOUR / months, 1),
        "podcast_h_per_mo": round(sum(r[MS] for r in pods) / HOUR / months, 1),
        "top": [[a, n] for a, n in top],
        "repeated": {"track": t_name, "artist": t_artist, "plays": t_plays,
                     "uri": uri},
        "signature": p["signature"],
        "new_pct": round(100 * new / len(music)),
        # Concentration and breadth are about the music. Counting podcast plays
        # in the denominator would read as the taste widening in the years he
        # listened to more talk, which is the opposite of what happened.
        "top5_pct": round(100 * sum(n for _, n in top[:5]) / len(music)),
        "artists": len(plays),
        "per_1k": round(1000 * len(plays) / len(music)),
        "device": f"{device} {round(100 * device_n / len(rows))}%",
        "peak_hour": Counter(r[HR] for r in rows).most_common(1)[0][0],
        "small_hours_pct": round(
            100 * sum(1 for r in rows if r[HR] < SMALL_HOURS) / len(rows), 1),
        "release_median": int(statistics.median(years)),
        "decades": shares([bands.get(i, 0) for i in range(len(BANDS) + 1)],
                          len(years)),
        "away": away,
        "defining": [[a, round(100 * share), round(ms / HOUR, 1)]
                     for a, ms, share in defining],
        "kids_note": p["kids_note"],
        "notes": p["notes"],
    }


def main():
    if not SEASONS:
        print("no seasons.json, so no Seasons page (see README)")
        return
    if not os.path.exists(DB):
        raise SystemExit(f"missing {DB} -- run python3 publish_db.py first")
    db = sqlite3.connect(DB)
    rows = load(db)

    # One pass over the whole history for the two figures that are about a
    # season's place in it rather than about the season: when an artist was
    # first heard, and how much of them was ever played.
    first_heard, lifetime = {}, defaultdict(lambda: [0, 0])
    for r in rows:
        if r[IS_LIVE] and r[KIND] == "track" and r[ARTIST]:
            first_heard.setdefault(r[ARTIST], r[DAY][:7])
            lifetime[r[ARTIST]][0] += r[MS]
            lifetime[r[ARTIST]][1] += 1

    # The last season is the one still being lived, so it runs to the newest
    # month a fresh export brings rather than to the month written above. A
    # month counts once the data reaches its 25th; a few days of a new month
    # would drag every per-month figure down.
    last = max(r[DAY] for r in rows)
    y, m = int(last[:4]), int(last[5:7])
    if int(last[8:10]) < 25:
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    SEASONS[-1]["to"] = max(SEASONS[-1]["to"], f"{y}-{m:02d}")

    out = []
    for p in SEASONS:
        window = [r for r in rows if p["from"] <= r[DAY][:7] <= p["to"]]
        out.append(figures([r for r in window if r[IS_LIVE]], window, p,
                           first_heard, lifetime))

    # The signature track is chosen by hand, so it is the one field here that
    # can quietly stop being true -- a re-tagged recording, a URI that no longer
    # appears in the window it was picked from.
    for p, f in zip(SEASONS, out):
        sig = p["signature"]
        if not sig:
            r = f["repeated"]
            f["signature"] = {"track": r["track"], "artist": r["artist"], "uri": r["uri"]}
            continue
        if not any(r[URI] == sig["uri"] for r in rows
                   if p["from"] <= r[DAY][:7] <= p["to"]):
            print(f"  {p['id']}: signature {sig['track']} is no longer "
                  f"played in {p['from']}-{p['to']}")

    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(f"web/phases.json  {os.path.getsize(OUT)/1e3:.0f} KB  {len(out)} seasons  "
          f"{out[0]['from']} - {out[-1]['to']}")
    for f in out:
        print(f"  {f['id']:>3}  {f['from']} {f['to']}  {f['plays']:>6} plays  "
              f"{f['artists']:>4} artists  {f['label']}")


if __name__ == "__main__":
    main()
