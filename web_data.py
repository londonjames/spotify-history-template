#!/usr/bin/env python3
"""Build web/index.json -- what the page needs before listening.db has arrived.

The page is served in two stages. This file is inlined into the HTML so search,
the leaderboards and the shape of sixteen years are on screen immediately;
listening.db (20 MB, 5 MB on the wire) loads in the background and then unlocks
the parts that need real SQL -- monthly detail for any one artist, albums,
episodes, and the question box.

So the rule for what belongs here is: everything the page shows before you have
clicked anything, and nothing else. Two consequences worth stating:

  * Per-entity counts are yearly, not monthly. Monthly for 45,855 tracks would be
    an order of magnitude more payload for detail nobody sees until they pick a
    track -- at which point the database can answer it exactly.
  * Tracks played once or twice are left out. There are 31,480 of them and they
    were 53% of the old 5.2 MB payload. They are still searchable; they are just
    searchable a second later, out of the database, rather than costing every
    visitor a two-second wait to find nothing.

Aggregate series, by contrast, ARE monthly. There are only 182 of them and they
are the whole point: the old page resolved everything to the calendar year, so
nothing that happened inside a year -- a trip, a phase, an album played to death
over one autumn -- could be seen at all.
"""
import collections
import datetime
import importlib.util
import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "web", "listening.db")
OUT = os.path.join(HERE, "web", "index.json")

# A track needs this many lifetime plays to be worth inlining. Everything below
# it lives in listening.db and appears in search once that has loaded.
MIN_TRACK_PLAYS = 3

# And an album this many. The floor is much higher than a track's because an
# album row carries a name, an artist and a second entry in albumInfo, so the
# cost per row is about twice a track's, and because a dozen plays spread over
# a twelve-track record is one listen rather than something he returned to.
# 15 plays would be 2,331 albums and 450 KB on a 2.7 MB payload; 35 is 1,224
# albums and 247 KB, and still reaches every record played through three times.
MIN_ALBUM_PLAYS = 35

# An episode needs this many. Higher than either of the others because an
# episode title is a sentence with three names in it -- a row here costs about
# three times a track's -- and because a podcast episode replayed once is the
# app resuming it, not him going back to it. 2 plays would be 1,404 episodes
# and 383 KB on a 3.0 MB payload; 3 would be 900 and 243 KB; 5 is 448 and
# 121 KB, and still reaches every episode he sat through a third time.
MIN_EPISODE_PLAYS = 5

_gspec = importlib.util.spec_from_file_location("_guests", os.path.join(HERE, "guests.py"))
_guests = importlib.util.module_from_spec(_gspec)
_gspec.loader.exec_module(_guests)


def month_index(first, last):
    """Every YYYY-MM from first to last inclusive, and a lookup into it."""
    y, m = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    months = []
    while (y, m) <= (ly, lm):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months, {k: i for i, k in enumerate(months)}


def sparse(counts, n):
    """Dense list -> "3:16|4:40". Most artists touch a handful of the 16 years."""
    return "|".join(f"{i}:{v}" for i, v in enumerate(counts) if v)


def pctile(sorted_vals, q):
    """Linear-interpolated percentile of an already-sorted list.

    Sitting lengths are heavily skewed -- a year holds hundreds of twenty-minute
    sittings and a handful of all-day ones -- so the median and the 90th are
    quoted together and both need to come off the same ruler.
    """
    if not sorted_vals:
        return 0.0
    i = q * (len(sorted_vals) - 1)
    lo = int(i)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def main():
    t0 = time.time()
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    meta = dict(c.execute("SELECT key, value FROM meta"))
    first, last = meta["first"], meta["last"]
    months, mi = month_index(first, last)
    y0, y1 = int(first[:4]), int(last[:4])
    years = list(range(y0, y1 + 1))
    yi = {y: i for i, y in enumerate(years)}

    def zeros(n):
        return [0] * n

    # ---- aggregate series, monthly and yearly ------------------------------
    # Three voices on one account, not two. "mine" excludes both the children
    # and the overnight speaker -- a nightly band of unattended playback that
    # from December 2025 reaches half of what would otherwise read as his.
    series = {k: zeros(len(months)) for k in ("all", "mine", "kids", "ambient")}
    # Hours carry the same four voices as plays. They used to carry only "all"
    # and "mine", which left the page deriving the children as all - mine; that
    # silently handed them the overnight speaker as well, so switching from
    # plays to hours moved 378 hours from nobody to the children.
    hours = {k: zeros(len(months)) for k in ("all", "mine", "kids", "ambient")}
    for day, kid, amb, n, ms in c.execute(
            """SELECT day, is_kid, is_ambient, COUNT(*), SUM(ms)
               FROM plays GROUP BY day, is_kid, is_ambient"""):
        i = mi[day[:7]]
        voice = "ambient" if amb else "kids" if kid else "mine"
        series["all"][i] += n
        series[voice][i] += n
        hours["all"][i] += ms
        hours[voice][i] += ms
    for k in hours:
        hours[k] = [round(v / 3.6e6, 1) for v in hours[k]]

    # ---- entity rows -------------------------------------------------------
    # [kind, name, sub, plays, minutes, "yIdx:n|...", first, last, skipPct,
    #  kidPlays, kidMins, ambPlays, ambMins]
    #
    # A row carries its own split rather than a flag saying whose it is. The
    # flag was MAX(is_kid), so one nursery play marked the whole artist as the
    # children's and the page then hid the row outright: Natalie Merchant has
    # 1,398 plays of his against 21 of theirs and did not appear at all. 47
    # artists were affected, 5,062 of his plays invisible. Ambient is split the
    # same way, because entity rows counted the overnight speaker as listening.
    rows = []

    def add(kind, name, sub, per_year, plays, ms, f, l, skip=None,
            kid_n=0, kid_ms=0, amb_n=0, amb_ms=0):
        rows.append([kind, name, sub, plays, round(ms / 60000),
                     sparse(per_year, len(years)), f, l,
                     -1 if skip is None else round(skip, 1),
                     kid_n, round(kid_ms / 60000),
                     amb_n, round(amb_ms / 60000)])

    def collect(sql, kind, min_plays=1):
        cur = collections.defaultdict(
            lambda: {"y": zeros(len(years)), "n": 0, "ms": 0, "sk": 0,
                     "kn": 0, "kms": 0, "an": 0, "ams": 0,
                     "f": "9999", "l": "0000", "sub": None})
        for r in c.execute(sql):
            # Keyed on name AND subtitle, never name alone: four different songs
            # are called "Come As You Are" and they are not the same recording.
            e = cur[(r["key"], r["sub"])]
            e["y"][yi[int(r["day"][:4])]] += r["n"]
            e["n"] += r["n"]
            e["ms"] += r["ms"]
            e["sk"] += r["sk"]
            e["kn"] += r["kid_n"]
            e["kms"] += r["kid_ms"] or 0
            e["an"] += r["amb_n"]
            e["ams"] += r["amb_ms"] or 0
            e["sub"] = r["sub"]
            e["f"] = min(e["f"], r["day"])
            e["l"] = max(e["l"], r["day"])
        for (name, _), e in cur.items():
            if e["n"] >= min_plays:
                add(kind, name, e["sub"] or "", e["y"], e["n"], e["ms"],
                    e["f"], e["l"], 100 * e["sk"] / e["n"],
                    e["kn"], e["kms"], e["an"], e["ams"])
        return sum(1 for e in cur.values() if e["n"] >= min_plays), len(cur)

    tracks_in, tracks_all = collect("""
        SELECT t.name AS key, a.name AS sub, p.day, COUNT(*) n, SUM(p.ms) ms,
               SUM(p.is_skip) sk,
               SUM(p.is_kid AND NOT p.is_ambient) kid_n,
               SUM(CASE WHEN p.is_kid AND NOT p.is_ambient THEN p.ms ELSE 0 END) kid_ms,
               SUM(p.is_ambient) amb_n,
               SUM(CASE WHEN p.is_ambient THEN p.ms ELSE 0 END) amb_ms
        FROM plays p JOIN tracks t ON t.id = p.track_id
        LEFT JOIN artists a ON a.id = t.artist_id
        GROUP BY t.id, p.day""", "t", MIN_TRACK_PLAYS)

    collect("""
        SELECT a.name AS key, NULL AS sub, p.day, COUNT(*) n, SUM(p.ms) ms,
               SUM(p.is_skip) sk,
               SUM(p.is_kid AND NOT p.is_ambient) kid_n,
               SUM(CASE WHEN p.is_kid AND NOT p.is_ambient THEN p.ms ELSE 0 END) kid_ms,
               SUM(p.is_ambient) amb_n,
               SUM(CASE WHEN p.is_ambient THEN p.ms ELSE 0 END) amb_ms
        FROM plays p JOIN artists a ON a.id = p.artist_id
        GROUP BY a.id, p.day""", "a")

    collect("""
        SELECT s.name AS key, NULL AS sub, p.day, COUNT(*) n, SUM(p.ms) ms,
               0 sk,
               SUM(p.is_kid AND NOT p.is_ambient) kid_n,
               SUM(CASE WHEN p.is_kid AND NOT p.is_ambient THEN p.ms ELSE 0 END) kid_ms,
               SUM(p.is_ambient) amb_n,
               SUM(CASE WHEN p.is_ambient THEN p.ms ELSE 0 END) amb_ms
        FROM plays p JOIN shows s ON s.id = p.show_id
        GROUP BY s.id, p.day""", "p")

    # ---- albums ------------------------------------------------------------
    # Every track has carried its album all along and nothing in the product
    # reached it, so a record played end to end over one autumn could only be
    # seen as a scatter of separate songs. An album is a fifth kind of row
    # rather than a key of its own, so search, the leaderboards, the routing
    # and the link previews pick it up without a special case anywhere.
    #
    # Grouped on album AND artist. "Greatest Hits" is forty different records,
    # and a soundtrack or a compilation shares one title across everyone on it.
    #
    # Spotify files a single as an album named after its one track, which would
    # otherwise put the same thing on the page twice under two kinds. The test
    # is that every track on the album shares the album's name, so a real album
    # with a title track keeps its other tracks and stays. Case and surrounding
    # space are ignored: the same single arrives punctuated both ways.
    SINGLES = """
        WITH singles AS (
            SELECT album, artist_id FROM tracks GROUP BY album, artist_id
            HAVING SUM(LOWER(TRIM(name)) <> LOWER(TRIM(album))) = 0)"""
    at = len(rows)
    albums_in, albums_all = collect(SINGLES + """
        SELECT t.album AS key, a.name AS sub, p.day, COUNT(*) n, SUM(p.ms) ms,
               SUM(p.is_skip) sk,
               SUM(p.is_kid AND NOT p.is_ambient) kid_n,
               SUM(CASE WHEN p.is_kid AND NOT p.is_ambient THEN p.ms ELSE 0 END) kid_ms,
               SUM(p.is_ambient) amb_n,
               SUM(CASE WHEN p.is_ambient THEN p.ms ELSE 0 END) amb_ms
        FROM plays p JOIN tracks t ON t.id = p.track_id
        JOIN artists a ON a.id = t.artist_id
        LEFT JOIN singles s ON s.album = t.album AND s.artist_id = t.artist_id
        WHERE TRIM(COALESCE(t.album, '')) <> '' AND s.album IS NULL
        GROUP BY t.album, t.artist_id, p.day""", "l", MIN_ALBUM_PLAYS)
    album_rows = rows[at:]
    album_keys = {(r[1], r[2]) for r in album_rows}

    # How many of those singles the test cost us, at the floor that is in force
    # -- the ones below it were never going to be rows either way.
    singles_cut = c.execute(SINGLES + """
        SELECT COUNT(*) FROM (
            SELECT COUNT(*) n FROM plays p JOIN tracks t ON t.id = p.track_id
            JOIN singles s ON s.album = t.album AND s.artist_id = t.artist_id
            GROUP BY t.album, t.artist_id) WHERE n >= ?""",
                            (MIN_ALBUM_PLAYS,)).fetchone()[0]

    # What a row cannot say on its own: how much of the record he has heard,
    # and when it was his. The peak month turns a lifetime total back into a
    # particular autumn. Keyed "<album>\0<artist>" because both halves are free
    # text and a NUL is the one character neither can contain.
    #
    # Caveat on the two track counts, and the page must not write around it:
    # tracks holds only what he has played -- 45,855 rows, 45,855 distinct
    # track_ids in plays -- so the database has no album listings and
    # tracksOnAlbum equals tracksHeard for all 1,224 of them. "Heard 9 of the
    # 12" cannot be said until a catalogue source fills the second number; both
    # are carried so the shape does not change when one arrives.
    #
    # The three figures that come off plays share one pass, grouped finely
    # enough to answer each of them. A second scan of plays joined to tracks
    # costs a quarter-second and this file has a three-second budget for
    # everything; the fourth figure is a cheap scan of tracks on its own.
    album_info = {}
    heard = collections.defaultdict(set)
    per_month = collections.defaultdict(collections.Counter)
    for alb, art, month, tid, n, f in c.execute("""
            SELECT t.album, a.name, substr(p.day, 1, 7) m, p.track_id,
                   COUNT(*), MIN(p.day)
            FROM plays p JOIN tracks t ON t.id = p.track_id
            JOIN artists a ON a.id = t.artist_id
            GROUP BY t.album, t.artist_id, m, p.track_id"""):
        if (alb, art) not in album_keys:
            continue
        k = alb + "\0" + art
        heard[k].add(tid)
        per_month[k][month] += n
        e = album_info.setdefault(k, [0, 0, f, ""])
        e[2] = min(e[2], f)
    for alb, art, total in c.execute("""
            SELECT t.album, a.name, COUNT(*) FROM tracks t
            JOIN artists a ON a.id = t.artist_id
            GROUP BY t.album, t.artist_id"""):
        e = album_info.get(alb + "\0" + art)
        if e:
            e[1] = total
    for k, e in album_info.items():
        e[0] = len(heard[k])
        # Ties go to the earlier month, which is the order they arrive in.
        e[3] = max(per_month[k].items(), key=lambda p: p[1])[0]

    # ---- episodes ----------------------------------------------------------
    # A show row says he has played 339 episodes of something and cannot say
    # which one he came back to. An episode is a sixth kind of row for the same
    # reason an album is a fifth: search, the leaderboards, the routing and the
    # link previews pick it up without a special case anywhere.
    #
    # Grouped on the episode id, and keyed on title AND show below it. Two shows
    # can both have an episode called "Introduction", and a show that has been
    # renamed or re-uploaded carries the same title twice.
    #
    # Private shows are already gone -- publish_db.py filters them out of the
    # fact table before listening.db is written -- so there is nothing to
    # re-filter here.
    et = len(rows)
    episodes_in, episodes_all = collect("""
        SELECT e.name AS key, s.name AS sub, p.day, COUNT(*) n, SUM(p.ms) ms,
               0 sk,
               SUM(p.is_kid AND NOT p.is_ambient) kid_n,
               SUM(CASE WHEN p.is_kid AND NOT p.is_ambient THEN p.ms ELSE 0 END) kid_ms,
               SUM(p.is_ambient) amb_n,
               SUM(CASE WHEN p.is_ambient THEN p.ms ELSE 0 END) amb_ms
        FROM plays p JOIN episodes e ON e.id = p.episode_id
        JOIN shows s ON s.id = e.show_id
        WHERE TRIM(e.name) <> ''
        GROUP BY e.id, p.day""", "e", MIN_EPISODE_PLAYS)
    episode_rows = rows[et:]

    # Keyed "<episode>\0<show>" like albumInfo, and for the same reason: both
    # halves are free text and a NUL is the one character neither can contain.
    # Unlike albumInfo none of it needs a second pass over plays -- the minutes
    # and the two dates are in the row already -- so this is the same three
    # figures under a key the page can reach by name rather than by row index.
    episode_info = {r[1] + "\0" + r[2]: [r[4], r[6], r[7]] for r in episode_rows}

    # ---- people, parsed out of episode titles ------------------------------
    # Spotify records no guest field, so guests.py reads them off the titles.
    # The show->person graph it produces is kept here rather than flattened to a
    # subtitle: who appeared where is the interesting part, and the old export
    # computed it in full and then threw it away.
    ep_rows = [(r["show"], r["episode"], r["day"], r["ms"], r["eid"],
                r["is_kid"], r["is_ambient"])
               for r in c.execute("""
        SELECT s.name show, e.name episode, p.day, p.ms, e.id eid,
               p.is_kid, p.is_ambient
        FROM plays p JOIN episodes e ON e.id = p.episode_id
        JOIN shows s ON s.id = e.show_id""")]
    bare = _guests.bare_name_shows([(s, e) for s, e, *_ in ep_rows])
    show_eps = collections.defaultdict(set)
    for s, e, *_ in ep_rows:
        show_eps[s].add(e)

    # Two passes. The first finds people where titles put a guest; the second
    # credits those same people wherever else a title names them, so an
    # episode about Elon Musk counts for him without his being on it.
    found = {}
    titles = {(s, e) for s, e, *_ in ep_rows}
    for s, e in titles:
        for name in _guests.extract(e, s in bare):
            found.setdefault(name.lower(), name)
    names_of = {}
    for s, e in titles:
        got = _guests.extract(e, s in bare)
        for name in _guests.mentions(e, found):
            if name.lower() not in {g.lower() for g in got}:
                got.append(name)
        names_of[(s, e)] = got

    people = {}
    for show, ep, day, ms, eid, kid, amb in ep_rows:
        for name in names_of[(show, ep)]:
            e = people.setdefault(name.lower(), {
                "n": name, "y": zeros(len(years)), "p": 0, "ms": 0,
                "f": day, "l": day, "shows": collections.defaultdict(set),
                "eps": {}, "kn": 0, "kms": 0, "an": 0, "ams": 0})
            e["f"], e["l"] = min(e["f"], day), max(e["l"], day)
            e["y"][yi[int(day[:4])]] += 1
            e["p"] += 1
            e["ms"] += ms
            # Split by voice like every other row. Without it Paw Patrol, read
            # out on a children's storytime show, ranked as one of his people.
            if amb:
                e["an"] += 1; e["ams"] += ms
            elif kid:
                e["kn"] += 1; e["kms"] += ms
            e["shows"][show].add(ep)
            # Kept by id as well as by title, against its last play. The graph
            # needs the titles per show; the page needs the ids, and a cap
            # needs to know which of them are the recent ones.
            e["eps"][eid] = max(e["eps"].get(eid, ""), day)

    graph = {}
    for e in people.values():
        roles = [("regular" if (len(eps) / max(1, len(show_eps[s])) >= 0.25
                                and len(eps) >= 5) else "guest", s, len(eps))
                 for s, eps in e["shows"].items()]
        regular = [r for r in roles if r[0] == "regular"]
        top = max(roles, key=lambda r: r[2])
        sub = ("Regular · " + regular[0][1]) if regular else ("Guest · " + top[1])
        if len(e["shows"]) > 1:
            sub += f" +{len(e['shows']) - 1}"
        add("g", e["n"], sub, e["y"], e["p"], e["ms"], e["f"], e["l"],
            kid_n=e["kn"], kid_ms=e["kms"], amb_n=e["an"], amb_ms=e["ams"])
        graph[e["n"]] = [[s, n, r] for r, s, n in sorted(roles, key=lambda r: -r[2])]

    # The episodes.id behind each person, so picking a name off the leaderboard
    # can ask the database a real question -- WHERE p.episode_id IN (...) --
    # rather than dead-ending. People are parsed out of titles and cannot be
    # joined to, so without this the page can compute nothing about one.
    #
    # 551 people and 1,662 links come to 18 KB, which is small enough that no
    # minimum-episode floor is worth the people it would drop: the tenth
    # biggest name has 23 episodes and the rest of the list has one or two
    # each, and one episode is exactly the case where the page cannot guess.
    # The cap below is a guard rather than a cut -- the largest person has 307
    # episodes today -- and peopleEpsCapped says whether it bit.
    PERSON_EPS_CAP = 400
    people_eps = {}
    people_eps_capped = False
    for e in people.values():
        eps = e["eps"]
        if len(eps) > PERSON_EPS_CAP:
            eps = sorted(eps, key=lambda i: eps[i], reverse=True)[:PERSON_EPS_CAP]
            people_eps_capped = True
        people_eps[e["n"]] = sorted(eps)

    rows.sort(key=lambda r: -r[3])

    # ---- facets ------------------------------------------------------------
    def facet(col, extra=""):
        return [list(r) for r in c.execute(
            f"""SELECT {col} k, SUM(is_kid=0 AND is_ambient=0) mine, SUM(is_kid=1) kids,
                       ROUND(SUM(ms)/3600000.0) hrs {extra}
                FROM plays WHERE {col} IS NOT NULL GROUP BY {col}
                ORDER BY COUNT(*) DESC""")]

    hour = {"mine": zeros(24), "kids": zeros(24)}
    for h, kid, n in c.execute("SELECT hour, is_kid, COUNT(*) FROM plays WHERE is_ambient=0 GROUP BY hour, is_kid"):
        hour["kids" if kid else "mine"][h] = n
    dow = {"mine": zeros(7), "kids": zeros(7)}
    for d, kid, n in c.execute("SELECT dow, is_kid, COUNT(*) FROM plays WHERE is_ambient=0 GROUP BY dow, is_kid"):
        dow["kids" if kid else "mine"][d] = n
    month = {"mine": zeros(12), "kids": zeros(12)}
    for m, kid, n in c.execute("""SELECT CAST(substr(day, 6, 2) AS INTEGER), is_kid, COUNT(*)
                                  FROM plays WHERE is_ambient=0 GROUP BY 1, is_kid"""):
        month["kids" if kid else "mine"][m - 1] = n

    # First time each artist was ever heard -- the discovery curve. Counted on
    # The owner's own listening; the kids arriving is not finding a new band.
    disc = zeros(len(months))
    for (day,) in c.execute("""SELECT MIN(day) FROM plays
                               WHERE is_kid=0 AND is_ambient=0 AND artist_id IS NOT NULL
                               GROUP BY artist_id"""):
        disc[mi[day[:7]]] += 1

    # ---- behaviour, not totals ---------------------------------------------
    # The sections below all describe his own listening, so they share one
    # filter. MINE is the house rule for whose ears a play belongs to. HEARD
    # adds the half-minute floor: a track dropped after ten seconds is evidence
    # about skipping, not about what he sat and listened to. Two sections below
    # deliberately use MINE without the floor, and say why where they do it.
    MINE = "is_kid=0 AND is_ambient=0"
    HEARD = MINE + " AND ms >= 30000"

    # ---- vintage: how old the records he plays are -------------------------
    # The gap in years between the play and the recording, averaged over the
    # calendar year. release_year is unknown for some of the history -- mostly
    # podcasts and things since delisted -- and those plays are left out rather
    # than counted as new releases, so the count travels with the number.
    vintage = {"years": [], "gap": [], "n": []}
    for y, gap, n in c.execute(f"""
            SELECT CAST(substr(day, 1, 4) AS INTEGER) y,
                   AVG(CAST(substr(day, 1, 4) AS INTEGER) - release_year),
                   COUNT(*)
            FROM plays WHERE {HEARD} AND release_year IS NOT NULL
            GROUP BY y ORDER BY y"""):
        vintage["years"].append(y)
        vintage["gap"].append(round(gap, 1))
        vintage["n"].append(n)

    # ---- repeats: days one track took over ---------------------------------
    # Ten plays of the same track in a day. Six was the first threshold, the
    # point where a track stops being a playlist passing through; he asked for
    # ten, which leaves only the days a song really was the day. The 60
    # biggest show the shape of it; the total travels with them so the page can
    # say how many days are behind the sixty rather than implying there are 60.
    repeats = {"total": c.execute(f"""
        SELECT COUNT(*) FROM (SELECT 1 FROM plays WHERE {HEARD}
                              AND track_id IS NOT NULL
                              GROUP BY track_id, day HAVING COUNT(*) >= 10)"""
                                 ).fetchone()[0],
               "days": [list(r) for r in c.execute(f"""
        SELECT p.day, t.name, a.name, COUNT(*) n
        FROM plays p JOIN tracks t ON t.id = p.track_id
        LEFT JOIN artists a ON a.id = t.artist_id
        WHERE {HEARD} GROUP BY p.track_id, p.day
        HAVING n >= 10 ORDER BY n DESC, p.day LIMIT 60""")]}

    # ---- sittings ----------------------------------------------------------
    # What the hour-of-day histogram cannot say: whether an evening was four
    # separate reaches for the phone or one unbroken run. A new sitting starts
    # when more than half an hour has passed since the previous play finished,
    # or when the date changes -- a run through midnight is split, which costs a
    # handful of late sittings and keeps every sitting attributable to one day.
    # Length is elapsed time from the first play's start to the last play's end,
    # so the pauses inside a sitting are part of it.
    #
    # The same pass also reads the music inside a sitting: what he reached for
    # first, and what he moved to next. Both are read off these sittings rather
    # than off a second reconstruction, so the page cannot end up quoting two
    # different sitting counts. Tracks only -- an artist is what is being
    # counted and a podcast has none -- so a podcast in the middle of a sitting
    # is stepped over rather than breaking the run of tracks in two.
    sessions = []
    openers = collections.Counter()
    handoffs = collections.Counter()
    cur = None
    end = 0.0
    prev_artist = None
    for day, sec, ms, kind, artist in c.execute(
            f"""SELECT p.day, p.sec, p.ms, p.kind, a.name
                FROM plays p LEFT JOIN artists a ON a.id = p.artist_id
                WHERE {HEARD} ORDER BY p.day, p.sec"""):
        if cur is not None and day == cur[0] and sec - end <= 1800:
            cur[2] += 1
        else:
            if cur is not None:
                sessions.append(cur)
            cur = [day, sec, 1, 0.0]
            prev_artist = None
        end = sec + ms / 1000.0
        cur[3] = (end - cur[1]) / 60.0
        if kind == "track" and artist:
            if prev_artist is None:
                openers[artist] += 1
            elif artist != prev_artist:
                # Ordered pairs, and only where the artist changes: a run of
                # six Taylor Swift tracks is one choice, not five handoffs.
                handoffs[(prev_artist, artist)] += 1
            prev_artist = artist
    if cur is not None:
        sessions.append(cur)

    opens = [[a, n] for a, n in openers.most_common(25)]
    hands = [[a, b, n] for (a, b), n in handoffs.most_common(25)]

    by_year = collections.defaultdict(list)
    for s in sessions:
        by_year[int(s[0][:4])].append(s[3])
    sess = {"years": [], "median": [], "p90": [], "count": [],
            "top": [[s[0], s[1], s[2], round(s[3], 1)]
                    for s in sorted(sessions, key=lambda s: -s[3])[:12]],
            "total": len(sessions)}
    for y in sorted(by_year):
        v = sorted(by_year[y])
        sess["years"].append(y)
        sess["median"].append(round(pctile(v, 0.5), 1))
        sess["p90"].append(round(pctile(v, 0.9), 1))
        sess["count"].append(len(v))

    # ---- trips -------------------------------------------------------------
    # The country facet says he has played music in 24 countries and cannot say
    # when or for how long. A trip is a run of plays in one country with no more
    # than two empty days inside it. Plays from elsewhere do not end a run --
    # the phone reports US on the way to the airport and again on the taxi home,
    # and a day in Japan carries a stray US play in the middle of it -- so the
    # gap is measured between that country's own plays. Fifteen plays is the
    # floor: below it a country is a layover, not a stay.
    #
    # No half-minute floor here. A trip is about being somewhere, and a track
    # skipped in a departure lounge is as much evidence of that as one played
    # through.
    trips = []
    run = None
    for country, day, artist, track in c.execute(f"""
            SELECT p.country, p.day, a.name, t.name
            FROM plays p LEFT JOIN artists a ON a.id = p.artist_id
            LEFT JOIN tracks t ON t.id = p.track_id
            WHERE {MINE} AND p.country IS NOT NULL AND p.country <> 'US'
            ORDER BY p.country, p.day, p.sec"""):
        ordinal = datetime.date.fromisoformat(day).toordinal()
        if run is None or run["c"] != country or ordinal - run["o"] > 3:
            run = {"c": country, "f": day, "l": day, "n": 0, "o": ordinal,
                   "a": collections.Counter(), "t": collections.Counter()}
            trips.append(run)
        run["l"], run["n"], run["o"] = day, run["n"] + 1, ordinal
        if artist:
            run["a"][artist] += 1
            # Tracks are counted under their artist, not on their own. Taken
            # separately the two lines of a trip contradict each other: the
            # August 2026 UK trip put Loreen at the top and a track nobody
            # would credit to her underneath, which reads as a bug.
            if track:
                run["t"][(artist, track)] += 1

    def headline(r):
        # Short trips tie a lot -- four plays of a top artist can be four
        # different tracks -- and the rows arrive in play order, so the tie
        # goes to whichever he reached for first rather than to the alphabet.
        if not r["a"]:
            return "", ""
        artist = r["a"].most_common(1)[0][0]
        by = [(t, n) for (a, t), n in r["t"].items() if a == artist]
        return artist, max(by, key=lambda p: p[1])[0] if by else ""

    trips = [[r["c"], r["f"], r["l"], r["n"], *headline(r)]
             for r in trips if r["n"] >= 15]
    # He is British, so the UK and the two next most frequent destinations
    # account for most of the rows. Flagging them lets the page offer
    # "everywhere else" without any trip being dropped from the payload.
    visits = collections.defaultdict(lambda: [0, 0])
    for t in trips:
        visits[t[0]][0] += 1
        visits[t[0]][1] += t[3]
    # Ranked on separate visits, with plays only as a tiebreak: two countries
    # reach three trips each and one round of listening should not settle it.
    frequent = set(sorted(visits, key=lambda k: (-visits[k][0], -visits[k][1],
                                                 k))[:3])
    for t in trips:
        t.append(1 if t[0] in frequent else 0)
    trips.sort(key=lambda t: t[1], reverse=True)

    # ---- shuffle -----------------------------------------------------------
    # Tracks only. Podcasts carry shuffle=0 always, so including them would read
    # as him turning shuffle off in the years he listened to more of them.
    # No half-minute floor either: shuffle is chosen when the queue starts, and
    # a shuffled queue skipped through is still a shuffled queue.
    shuffle = {"years": [], "pct": []}
    for y, pct in c.execute(f"""
            SELECT CAST(substr(day, 1, 4) AS INTEGER) y,
                   100.0 * SUM(shuffle) / COUNT(*)
            FROM plays WHERE {MINE} AND kind = 'track'
            GROUP BY y ORDER BY y"""):
        shuffle["years"].append(y)
        shuffle["pct"].append(round(pct, 1))

    # ---- streaks -----------------------------------------------------------
    # A day counts if it holds one play he listened to, so this is about
    # showing up rather than volume. daysTotal is every date from his first
    # play to his last, including the ones with nothing on them -- the point of
    # daysCovered is the fraction, and a denominator of only the days he played
    # would make it 100%.
    days = [r[0] for r in c.execute(
        f"SELECT DISTINCT day FROM plays WHERE {HEARD} ORDER BY day")]
    ords = [datetime.date.fromisoformat(d).toordinal() for d in days]
    best_len, best_at, cur_len = 1, 0, 1
    gap_len, gap_at = 0, 0
    for i in range(1, len(ords)):
        step = ords[i] - ords[i - 1]
        cur_len = cur_len + 1 if step == 1 else 1
        if cur_len > best_len:
            best_len, best_at = cur_len, i - cur_len + 1
        if step - 1 > gap_len:
            gap_len, gap_at = step - 1, i

    def shift(day, n):
        return (datetime.date.fromisoformat(day)
                + datetime.timedelta(days=n)).isoformat()

    streaks = {
        "longestDays": best_len,
        "longestFrom": days[best_at], "longestTo": days[best_at + best_len - 1],
        # The gap is quoted as the silence itself, first empty day to last, so
        # its length is the number of days between those two dates inclusive.
        "longestGap": gap_len,
        "gapFrom": shift(days[gap_at - 1], 1), "gapTo": shift(days[gap_at], -1),
        "daysCovered": len(days), "daysTotal": ords[-1] - ords[0] + 1,
    }

    # ---- artists who are really one song -----------------------------------
    # 150 plays is enough that the artist is not an accident, and 55% on one
    # track is where a body of work turns into a single song on repeat. Both
    # numbers are per track_id, so the editions already merged upstream are not
    # split back apart and made to look like variety.
    onesong = [list(r) for r in c.execute(f"""
        WITH per AS (
            SELECT artist_id aid, track_id tid, COUNT(*) n FROM plays
            WHERE {HEARD} AND artist_id IS NOT NULL AND track_id IS NOT NULL
            GROUP BY artist_id, track_id),
        tot AS (SELECT aid, SUM(n) n FROM per GROUP BY aid HAVING SUM(n) >= 150),
        best AS (SELECT aid, tid, n,
                        ROW_NUMBER() OVER (PARTITION BY aid ORDER BY n DESC) rk
                 FROM per)
        SELECT a.name, tot.n, t.name, best.n
        FROM tot JOIN best ON best.aid = tot.aid AND best.rk = 1
        JOIN artists a ON a.id = tot.aid JOIN tracks t ON t.id = best.tid
        WHERE best.n * 100 >= 55 * tot.n
        ORDER BY tot.n DESC LIMIT 40""")]

    stats = {k: c.execute(q).fetchone()[0] for k, q in {
        "plays": "SELECT COUNT(*) FROM plays",
        "minePlays": "SELECT COUNT(*) FROM plays WHERE is_kid=0 AND is_ambient=0",
        "ambient": "SELECT COUNT(*) FROM plays WHERE is_ambient=1",
        "tracks": "SELECT COUNT(*) FROM tracks",
        "artists": "SELECT COUNT(*) FROM artists",
        "shows": "SELECT COUNT(*) FROM shows",
        "episodes": "SELECT COUNT(*) FROM episodes",
    }.items()}
    stats["kidPlays"] = stats["plays"] - stats["minePlays"] - stats["ambient"]
    stats["people"] = len(people)
    stats["hours"] = round(sum(hours["all"]))
    stats["mineHours"] = round(sum(hours["mine"]))
    stats["kidHours"] = round(sum(hours["kids"]))
    stats["ambientHours"] = round(sum(hours["ambient"]))
    # The artists table counts every artist on the account, the children's and
    # the overnight speaker's included. These two count his: how many he has
    # heard at all, and how many he heard once and never went back to.
    stats["artistsEver"], stats["artistsOnce"] = c.execute(f"""
        SELECT COUNT(*), SUM(n = 1) FROM (
            SELECT COUNT(*) n FROM plays
            WHERE {HEARD} AND artist_id IS NOT NULL GROUP BY artist_id)"""
                                                           ).fetchone()
    stats["sessions"] = len(sessions)
    stats["peopleEpsCapped"] = people_eps_capped

    payload = {
        "meta": {"first": first, "last": last, "tz": meta["timezone"],
                 "built": meta["built"]},
        "y0": y0, "years": years, "months": months,
        "stats": stats, "series": series, "hours": hours,
        "cols": ["kind", "name", "sub", "plays", "minutes", "years",
                 "first", "last", "skipPct",
                 "kidPlays", "kidMins", "ambPlays", "ambMins"],
        "rows": rows, "albumInfo": album_info,
        "episodeInfo": episode_info,
        "facets": {"hour": hour, "dow": dow, "month": month, "discovery": disc,
                   "device": facet("device"), "country": facet(
                       "country", ", MIN(day) f, MAX(day) l")},
        "graph": graph, "peopleEps": people_eps,
        "openers": opens, "handoffs": hands,
        "vintage": vintage, "repeats": repeats, "sessions": sess,
        "trips": trips, "shuffle": shuffle, "onesong": onesong,
        "streaks": streaks,
    }
    # Written by cities.py, which resolves the ip_addr on every play against a
    # local copy of the DB-IP city table. It is a separate step because it is
    # slow and only changes when a new export lands; the addresses themselves
    # never leave that script.
    cities_path = os.path.join(os.path.dirname(OUT), "cities.json")
    if os.path.exists(cities_path):
        payload["cities"] = json.load(open(cities_path, encoding="utf-8"))
    away_path = os.path.join(os.path.dirname(OUT), "away.json")
    if os.path.exists(away_path):
        payload["away"] = json.load(open(away_path, encoding="utf-8"))
    # The world as dots, for the map on a day spent away (worldmap.py).
    land_path = os.path.join(os.path.dirname(OUT), "land.json")
    if os.path.exists(land_path):
        payload["land"] = json.load(open(land_path, encoding="utf-8"))
    js = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    album_bytes = sum(len(json.dumps(v, separators=(",", ":"),
                                     ensure_ascii=False).encode())
                      for v in (album_rows, album_info))
    episode_bytes = sum(len(json.dumps(v, separators=(",", ":"),
                                       ensure_ascii=False).encode())
                        for v in (episode_rows, episode_info))
    open(OUT, "w").write(js)
    print(f"web/index.json  {len(js.encode())/1e6:.1f} MB  in {time.time()-t0:.1f}s")
    print(f"  {len(rows):,} rows  ({tracks_in:,} of {tracks_all:,} tracks inlined, "
          f"the rest live in listening.db)")
    print(f"  albums    {albums_in:,} of {albums_all:,} at {MIN_ALBUM_PLAYS}+ "
          f"plays  {singles_cut} singles dropped  "
          f"+{album_bytes/1e3:.0f} KB of the payload")
    print(f"  episodes  {episodes_in:,} of {episodes_all:,} at "
          f"{MIN_EPISODE_PLAYS}+ plays  "
          f"+{episode_bytes/1e3:.0f} KB of the payload")
    print(f"  {len(months)} months  {stats['artists']:,} artists  "
          f"{stats['people']} people  {len(payload['facets']['country'])} countries")
    print(f"  vintage   {len(vintage['years'])} years  "
          f"{vintage['gap'][0]} -> {vintage['gap'][-1]} years behind")
    print(f"  repeats   {len(repeats['days'])} of {repeats['total']} days, "
          f"top {repeats['days'][0][3]} plays of one track")
    print(f"  sessions  {len(sessions):,} sittings  "
          f"median {sess['median'][-1]} min in {sess['years'][-1]}")
    print(f"  people    {len(people_eps)} with episode ids  "
          f"{sum(len(v) for v in people_eps.values()):,} links"
          f"{'  (capped)' if people_eps_capped else ''}")
    print(f"  openers   {opens[0][0]} starts {opens[0][1]:,} of "
          f"{len(sessions):,} sittings  handoffs {hands[0][0]} -> "
          f"{hands[0][1]} {hands[0][2]}")
    print(f"  trips     {len(trips)} of 15+ plays  "
          f"{len({t[0] for t in trips})} countries  "
          f"{sum(t[6] for t in trips)} to {', '.join(sorted(frequent))}")
    print(f"  shuffle   {shuffle['pct'][0]}% in {shuffle['years'][0]} -> "
          f"{shuffle['pct'][-1]}% in {shuffle['years'][-1]}")
    print(f"  onesong   {len(onesong)} artists  "
          f"{stats['artistsOnce']:,} of {stats['artistsEver']:,} heard once")
    print(f"  streaks   {streaks['longestDays']} days to "
          f"{streaks['longestTo']}  longest silence {streaks['longestGap']} "
          f"days  {streaks['daysCovered']:,} of {streaks['daysTotal']:,} days")


if __name__ == "__main__":
    main()
