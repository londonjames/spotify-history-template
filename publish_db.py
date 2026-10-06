#!/usr/bin/env python3
"""Build web/listening.db -- the database the published page is allowed to hold.

The site used to be safe by omission. private.py filtered shows out of each
collector, and anything no collector emitted (audiobooks) never reached a page
because nothing asked for it. That works right up until something does ask, and
the question box asks for everything.

So this builds a second database containing only what may be published, and the
browser gets that file rather than a route into spotify.db. The guarantee is
physical: a private show is not filtered out of the answer, it is not in the
file being queried. No prompt can extract what was never shipped.

What comes across:
  * the account's owner only -- other family profiles are not history
  * no private shows, no private audiobooks (private.py, COALESCE(show, audiobook))
  * no audiobooks at all: 102 plays of 51 titles, mostly one-play samples from a
    single browse, too thin to be worth a category and too easy to get wrong
  * no `episode = show` rows, which are Spotify's 2018 music videos misfiled

Shape is a star schema: four dictionary tables and one fact table. The strings
are the whole cost of the file -- 1.72 MB across every distinct track, artist,
album and episode title -- and saying each of them once instead of 299k times is
what makes a queryable history small enough to ship to a browser.

    python3 publish_db.py
"""
import json
import importlib.util
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "spotify.db")
OUT = os.path.join(HERE, "web", "listening.db")
import config
WHO = config.NAME


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_{name}", os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_private = _load("private")
_sh = _load("sh")

SCHEMA = """
CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
-- uri is carried so the story page can embed the actual recording. It is on
-- tracks, not on plays: 45,855 URIs against 298,675 rows.
CREATE TABLE tracks  (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                      artist_id INTEGER NOT NULL, album TEXT, uri TEXT);
CREATE TABLE shows   (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE episodes(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                      show_id INTEGER NOT NULL);

CREATE TABLE plays (
  day TEXT NOT NULL,          -- YYYY-MM-DD, local time
  hour INTEGER NOT NULL,      -- 0-23, local time
  -- Seconds since local midnight. The export timestamps every play to the
  -- second and this file used to round them to the hour, which threw away the
  -- only thing that makes a sitting a sitting: two plays four minutes apart and
  -- two plays fifty minutes apart became the same fact. Everything that reads
  -- as behaviour rather than totals -- what follows what, what gets played
  -- twice in a row, where a listening run starts and stops -- needs this.
  sec INTEGER NOT NULL,
  dow INTEGER NOT NULL,       -- 0 = Monday, local time
  ms INTEGER NOT NULL,
  kind TEXT NOT NULL,         -- 'track' | 'podcast'
  track_id INTEGER, artist_id INTEGER,
  show_id INTEGER, episode_id INTEGER,
  country TEXT, device TEXT,
  -- The year the recording came out. Spotify has emptied its genre data, so
  -- this is what is left to say what a stretch of listening sounded like --
  -- and it says more anyway: the era someone reaches for is a mood.
  release_year INTEGER,
  shuffle INTEGER NOT NULL, is_skip INTEGER NOT NULL, is_kid INTEGER NOT NULL,
  -- A speaker playing to an empty room, not a person listening. See below.
  is_ambient INTEGER NOT NULL
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

-- Cover art and artist photographs, as urls on Spotify's own image host. Kept
-- here rather than in the inlined payload because it is a few hundred KB that
-- only matters once something is on screen, and the page has this file by then.
-- art.py fills art_cache.json from the API; this table is written from that
-- cache on every build, so a rebuild never costs a request.
CREATE TABLE art (kind TEXT NOT NULL, key TEXT NOT NULL, url TEXT NOT NULL,
                  PRIMARY KEY (kind, key)) WITHOUT ROWID;
"""

# Only the dictionaries are indexed. The fact table is not, deliberately: the
# browser loads the whole file into memory and every question worth asking of a
# listening history is an aggregate that scans most of it anyway, so an index
# would be read past rather than used. Indexing plays cost 35 MB against a 15 MB
# table -- more than twice the data, to make full scans that are already fast in
# memory no faster.
INDEXES = """
CREATE INDEX ix_a_name   ON artists(name);
CREATE INDEX ix_t_name   ON tracks(name);
CREATE INDEX ix_t_artist ON tracks(artist_id);
CREATE INDEX ix_e_show   ON episodes(show_id);
"""

def display_names(src, key_col, name_col, where):
    """Map a normalised key to the spelling it was played under most.

    Spotify files one recording under several names -- "Wonderwall",
    "Wonderwall - Remastered", "Wonderwall (Remastered 2014)" -- and load.py
    already collapsed those to one normalised key. Picking the most-played
    spelling means the page shows the name he actually saw, not whichever
    variant happened to sort first.
    """
    best = {}
    for key, name, n in src.execute(
            f"""SELECT {key_col}, {name_col}, COUNT(*) FROM plays
                WHERE {where} AND {key_col} IS NOT NULL AND {name_col} IS NOT NULL
                GROUP BY {key_col}, {name_col}"""):
        if key not in best or n > best[key][1]:
            best[key] = (name, n)
    return {k: v[0] for k, v in best.items()}


ART_CACHE = os.path.join(HERE, "art_cache.json")


def write_art(out):
    """Copy art_cache.json into the art table, keyed the way the page asks.

    The cache is keyed on Spotify's own track id, because that is what the API
    was asked about. The page knows a name and an artist, so the keys are
    translated here, once, rather than shipping a second id column to every
    visitor and making the page join on it.

    The separator is a unit separator, not a null. sql.js reads a TEXT value as
    a C string and stops at the first null byte, so a key joined with one
    arrived in the browser as the track name alone and matched nothing, while
    looking perfectly correct in the file.
    """
    SEP = "\x1f"
    if not os.path.exists(ART_CACHE):
        print("  no art_cache.json; run art.py")
        return
    cache = json.load(open(ART_CACHE))
    rows = []

    by_uri = {}
    for uri, name, artist in out.execute("""
            SELECT t.uri, t.name, ifnull(a.name, '')
              FROM tracks t LEFT JOIN artists a ON a.id = t.artist_id
             WHERE t.uri IS NOT NULL"""):
        by_uri[uri.split(":")[-1]] = f"{name}{SEP}{artist}"
    for sid, url in (cache.get("t") or {}).items():
        if url and sid in by_uri:
            rows.append(("t", by_uri[sid], url))

    for key, url in (cache.get("l") or {}).items():
        if url:
            rows.append(("l", key.replace("\x00", SEP), url))
    for name, url in (cache.get("a") or {}).items():
        if url:
            rows.append(("a", name, url))
    # Shows are keyed on the name the export uses, which is the same name the
    # page has in hand for a podcast row and can parse out of a guest's
    # subtitle, so no translation is needed here.
    for name, url in (cache.get("p") or {}).items():
        if url:
            rows.append(("p", name, url))
    # People are keyed on the name the page shows, as people_art.py stored it.
    for name, url in (cache.get("g") or {}).items():
        if url:
            rows.append(("g", name, url))

    out.executemany("INSERT OR REPLACE INTO art VALUES (?,?,?)", rows)
    kinds = {}
    for k, _, _ in rows:
        kinds[k] = kinds.get(k, 0) + 1
    print("  art: " + ", ".join(f"{v:,} {k}" for k, v in sorted(kinds.items())))


def main():
    t0 = time.time()
    if not os.path.exists(DB):
        sys.exit(f"missing {DB} -- run ./refresh first")
    src = sqlite3.connect(DB)

    # The exclusion is registered as a SQL function and applied in the SELECT
    # that fills the fact table, so there is exactly one place a private title
    # could get through, rather than one per collector.
    blocked = _private.make_filter()
    src.create_function("is_private", 1, lambda s: 1 if blocked(s) else 0)

    # kids.txt is a three-part grammar (artist, "Artist :: Track", "~ Album
    # prefix") and sh.py already expresses it as SQL, including the rule that a
    # recording which ever appeared on a kids' album stays the kids' everywhere.
    # Reused rather than reimplemented -- there were two copies of this already.
    kid_sql, kid_params = _sh.kids_predicate()

    PUBLISHABLE = f"""
        who = '{WHO}'
        AND kind IN ('track', 'podcast')
        AND NOT is_private(COALESCE(show, audiobook))
        AND (episode IS NULL OR episode <> show)
    """

    # Playback nobody was listening to: a speaker left on overnight in another
    # room, say. It is counted under "Everyone" and left out of the owner's own
    # lists, like the children's listening. Off unless config.json gives a rule
    # ("ambient_sql"), which is a condition over the plays table, for example
    #   "(device = 'Unknown' AND (hour_local >= 23 OR hour_local < 6))"
    AMBIENT = config._c.get("ambient_sql") or "0"

    if os.path.exists(OUT):
        os.remove(OUT)
    out = sqlite3.connect(OUT)
    out.executescript(SCHEMA)

    # ---- dictionaries -----------------------------------------------------
    artist_name = display_names(src, "artist_norm", "artist", PUBLISHABLE)
    track_name = display_names(src, "track_norm", "track", PUBLISHABLE)

    artist_id = {k: i for i, k in enumerate(sorted(artist_name), 1)}
    out.executemany("INSERT INTO artists VALUES (?,?)",
                    [(i, artist_name[k]) for k, i in artist_id.items()])

    # A recording is (title, artist): two artists can have a song of the same
    # name, and the same artist's live and studio takes are separate rows.
    # The URI of the edition actually played most, so an embed points at the
    # recording he heard rather than a remaster that happens to sort first.
    uri_of = {}
    for tn, an, uri, n in src.execute(
            f"""SELECT track_norm, artist_norm, track_uri, COUNT(*) FROM plays
                WHERE {PUBLISHABLE} AND track_norm IS NOT NULL AND track_uri IS NOT NULL
                GROUP BY track_norm, artist_norm, track_uri"""):
        k = (tn, an)
        if k not in uri_of or n > uri_of[k][1]:
            uri_of[k] = (uri, n)

    album_of = {}
    for tn, an, album, n in src.execute(
            f"""SELECT track_norm, artist_norm, album, COUNT(*) FROM plays
                WHERE {PUBLISHABLE} AND track_norm IS NOT NULL AND album IS NOT NULL
                GROUP BY track_norm, artist_norm, album"""):
        k = (tn, an)
        if k not in album_of or n > album_of[k][1]:
            album_of[k] = (album, n)

    track_keys = sorted({(tn, an) for tn, an in src.execute(
        f"""SELECT DISTINCT track_norm, artist_norm FROM plays
            WHERE {PUBLISHABLE} AND track_norm IS NOT NULL""")})
    track_id = {k: i for i, k in enumerate(track_keys, 1)}
    out.executemany("INSERT INTO tracks VALUES (?,?,?,?,?)",
                    [(i, track_name.get(tn, tn), artist_id.get(an),
                      album_of.get((tn, an), (None,))[0],
                      uri_of.get((tn, an), (None,))[0])
                     for (tn, an), i in track_id.items()])

    show_keys = sorted({r[0] for r in src.execute(
        f"SELECT DISTINCT show FROM plays WHERE {PUBLISHABLE} AND show IS NOT NULL")})
    show_id = {k: i for i, k in enumerate(show_keys, 1)}
    out.executemany("INSERT INTO shows VALUES (?,?)",
                    [(i, k) for k, i in show_id.items()])

    ep_keys = sorted({(e, s) for e, s in src.execute(
        f"""SELECT DISTINCT episode, show FROM plays
            WHERE {PUBLISHABLE} AND episode IS NOT NULL AND show IS NOT NULL""")})
    ep_id = {k: i for i, k in enumerate(ep_keys, 1)}
    out.executemany("INSERT INTO episodes VALUES (?,?,?)",
                    [(i, e, show_id[s]) for (e, s), i in ep_id.items()])

    # ---- facts ------------------------------------------------------------
    rows = src.execute(f"""
        SELECT day_local, hour_local,
               -- Local offsets here are whole hours, so the minutes and seconds
               -- of the UTC stamp are the local ones.
               hour_local * 3600
                 + CAST(substr(ts, 15, 2) AS INTEGER) * 60
                 + CAST(substr(ts, 18, 2) AS INTEGER),
               dow_local, ms_played, kind,
               track_norm, artist_norm, show, episode,
               country, device, release_year, shuffle, is_skip,
               CASE WHEN {kid_sql} THEN 1 ELSE 0 END,
               CASE WHEN {AMBIENT} THEN 1 ELSE 0 END
        FROM plays WHERE {PUBLISHABLE}""", kid_params)

    def facts():
        for (day, hr, sec, dw, ms, kind, tn, an, show, ep,
             country, device, year, shuf, skip, kid, amb) in rows:
            yield (day, hr, sec, dw, ms, kind,
                   track_id.get((tn, an)), artist_id.get(an),
                   show_id.get(show), ep_id.get((ep, show)),
                   # ZZ is Spotify's "we don't know", not a country. Stored as
                   # NULL so a map never draws it and a COUNT never counts it.
                   None if country in (None, "", "ZZ") else country,
                   # iOS is the iPhone app before Spotify began naming the
                   # hardware; Unknown and Cast say nothing about where it played.
                   None if device in ("Unknown", "Cast") else
                   "iPhone" if device == "iOS" else device,
                   # 1900 and earlier is Spotify filling in a blank, not a 78.
                   year if year and year > 1940 else None,
                   shuf, skip, kid, amb)

    out.executemany(
        "INSERT INTO plays VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", facts())
    out.executescript(INDEXES)
    write_art(out)

    n, first, last, hours = out.execute(
        "SELECT COUNT(*), MIN(day), MAX(day), SUM(ms)/3600000.0 FROM plays").fetchone()
    out.executemany("INSERT INTO meta VALUES (?,?)", [
        ("built", time.strftime("%Y-%m-%dT%H:%M:%S")),
        ("who", WHO), ("plays", n), ("first", first), ("last", last),
        ("hours", round(hours)), ("timezone", config.TZ),
        ("ambient", out.execute(
            "SELECT COUNT(*) FROM plays WHERE is_ambient=1").fetchone()[0]),
    ])
    out.commit()
    out.execute("VACUUM")
    out.close()

    size = os.path.getsize(OUT)
    print(f"web/listening.db  {size/1e6:.1f} MB  in {time.time()-t0:.1f}s")
    print(f"  {n:,} plays  {first} -> {last}  {hours:,.0f} hours")
    print(f"  {len(artist_id):,} artists  {len(track_id):,} tracks  "
          f"{len(show_id)} shows  {len(ep_id):,} episodes")


if __name__ == "__main__":
    main()
