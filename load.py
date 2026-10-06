#!/usr/bin/env python3
"""Ingest Spotify extended-streaming-history exports into a local SQLite DB.

Designed to be run again every time a new export arrives. Spotify hands you the
full history each time, so exports overlap almost entirely -- this loads every
export it can find and de-duplicates, so re-running is always safe and always
additive. Nothing is ever dropped unless you pass --rebuild.

  python3 load.py                 discover + ingest everything new
  python3 load.py --rebuild       wipe and reload from scratch
  python3 load.py path/to/export  ingest one specific export

Where it looks, in order:
  1. ~/spotify-history/exports/*          (folders or .zip -- the permanent home)
  2. ~/Downloads/my_spotify_data*         (folders or .zip -- where Spotify puts it)

A .zip found in Downloads is unpacked into exports/ and kept, so the raw export
survives even after Downloads gets cleaned out.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from zoneinfo import ZoneInfo

import config
import devices

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "spotify.db")
EXPORTS = os.path.join(HERE, "exports")

# Spotify timestamps are UTC. Stored raw in ts/hour/dow, and converted to
# local time in hour_local/dow_local, because "when do you listen" is a
# question about your evening, not about Greenwich. One home zone (config.json)
# is used for the whole history; trips are days long and blur very little.
HOME_TZ = ZoneInfo(config.TZ)
UTC = datetime.timezone.utc
DOWNLOADS = os.path.expanduser("~/Downloads")

# Spotify splits one recording across "Wonderwall", "Wonderwall - Remastered",
# "Wonderwall (Remastered 2014)" etc. Collapse only edition/master variants --
# live takes, acoustics, remixes and covers stay distinct recordings.
_EDITION = re.compile(
    r"""\s*(?:-|–|\(|\[)\s*(
        (?:\d{4}\s+)?re-?master(?:ed)?(?:\s+(?:version|\d{4}))?
      | (?:digitally\s+)?remaster(?:ed)?[^)\]]*
      | deluxe(?:\s+edition)? | expanded(?:\s+edition)? | bonus\s+track
      | album\s+version | single\s+version | radio\s+edit
      | mono(?:\s+version)? | stereo(?:\s+version)?
      | (?:\d+(?:st|nd|rd|th)\s+)?anniversary(?:\s+(?:edition|version))?[^)\]]*
      | \d{4}\s+version
    )\s*(?:\)|\]|$)""",
    re.I | re.X,
)


def strip_edition(t):
    """Drop edition labels but keep the original casing -- for display."""
    if not t:
        return t
    prev = None
    while prev != t:
        prev = t
        t = _EDITION.sub("", t).strip(" -–([")
    return " ".join(t.split())


def norm_title(t):
    """Lowercased edition-stripped form -- the grouping key."""
    s = strip_edition(t)
    return s.lower() if s else s


def norm_artist(a):
    return " ".join(a.lower().split()) if a else a


SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
  id INTEGER PRIMARY KEY,
  who TEXT NOT NULL,
  ts TEXT NOT NULL,
  year INTEGER, month INTEGER, day TEXT, hour INTEGER, dow INTEGER,
  hour_local INTEGER, dow_local INTEGER, day_local TEXT,
  ms_played INTEGER NOT NULL,
  kind TEXT,
  track TEXT, artist TEXT, album TEXT, track_uri TEXT,
  track_norm TEXT, artist_norm TEXT,
  episode TEXT, show TEXT, episode_uri TEXT,
  audiobook TEXT, chapter TEXT,
  platform TEXT, country TEXT,
  reason_start TEXT, reason_end TEXT,
  shuffle INTEGER, skipped INTEGER, offline INTEGER, incognito INTEGER,
  device TEXT, is_skip INTEGER
);

-- The de-dup contract. Two exports describe the same play with the same
-- person, timestamp, duration and URI, so INSERT OR IGNORE makes re-ingesting
-- an overlapping export a no-op.
CREATE UNIQUE INDEX IF NOT EXISTS ux_play
  ON plays(who, ts, ms_played, COALESCE(track_uri, episode_uri, ''));

"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_plays_who_year   ON plays(who, year);
CREATE INDEX IF NOT EXISTS ix_plays_artist     ON plays(artist);
CREATE INDEX IF NOT EXISTS ix_plays_track      ON plays(track);
CREATE INDEX IF NOT EXISTS ix_plays_norm       ON plays(track_norm, artist_norm);
CREATE INDEX IF NOT EXISTS ix_plays_artistnorm ON plays(artist_norm);
CREATE INDEX IF NOT EXISTS ix_plays_track_uri  ON plays(track_uri);
CREATE INDEX IF NOT EXISTS ix_plays_show       ON plays(show);
CREATE INDEX IF NOT EXISTS ix_plays_ts         ON plays(ts);
CREATE INDEX IF NOT EXISTS ix_plays_kind_year  ON plays(kind, year);
CREATE INDEX IF NOT EXISTS ix_plays_device     ON plays(device);
CREATE INDEX IF NOT EXISTS ix_plays_daylocal   ON plays(day_local);
"""

TABLES_TAIL = """
-- Provenance: which export each file came from, so a re-run can skip files it
-- has already seen byte-for-byte and you can audit where a play came from.
CREATE TABLE IF NOT EXISTS sources (
  sha1 TEXT PRIMARY KEY,
  export TEXT, file TEXT, who TEXT,
  rows_in_file INTEGER, rows_added INTEGER,
  first_ts TEXT, last_ts TEXT,
  ingested_at TEXT
);
"""


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack_zips():
    """Move any Spotify .zip out of Downloads into exports/ and unpack it."""
    os.makedirs(EXPORTS, exist_ok=True)
    for z in glob.glob(os.path.join(DOWNLOADS, "my_spotify_data*.zip")):
        stamp = datetime.date.fromtimestamp(os.path.getmtime(z)).isoformat()
        dest = os.path.join(EXPORTS, f"{stamp}-{os.path.basename(z)[:-4]}")
        if os.path.exists(dest):
            continue
        print(f"  unpacking {os.path.basename(z)} -> exports/{os.path.basename(dest)}")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(dest)


def discover(explicit=None):
    """Return every export root we should ingest, newest last."""
    if explicit:
        return [os.path.abspath(explicit)]
    unpack_zips()
    roots = []
    if os.path.isdir(EXPORTS):
        roots += [os.path.join(EXPORTS, d) for d in os.listdir(EXPORTS)
                  if os.path.isdir(os.path.join(EXPORTS, d))]
    roots += [d for d in glob.glob(os.path.join(DOWNLOADS, "my_spotify_data*"))
              if os.path.isdir(d)]
    # An export root is whatever directory holds the streaming-history folders.
    out = []
    for r in roots:
        if glob.glob(os.path.join(r, "**", "Streaming_History_*.json"), recursive=True):
            out.append(r)
    return sorted(set(out), key=os.path.getmtime)


def profiles(root):
    """Map each sub-folder to a person, using the export's own index.txt."""
    out = {}
    idx = os.path.join(root, "index.txt")
    dirs = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
    if os.path.exists(idx):
        for line in open(idx):
            line = line.strip()
            if not line:
                continue
            for d in sorted(dirs, key=len, reverse=True):
                if line.startswith(d):
                    name = line[len(d):].strip()
                    out[d] = config.NAME if name.lower() in ("you", "") else name
                    break
    # A single-account export has no index.txt: everything in it is the owner's.
    for d in dirs:
        out.setdefault(d, config.NAME if not os.path.exists(idx) else d)
    return out


def kind_of(r):
    if r.get("spotify_track_uri"):
        return "track"
    if r.get("spotify_episode_uri"):
        return "podcast"
    if r.get("audiobook_uri"):
        return "audiobook"
    return "unknown"


COLS = ("who,ts,year,month,day,hour,dow,hour_local,dow_local,day_local,"
        "ms_played,kind,track,artist,album,track_uri,"
        "track_norm,artist_norm,episode,show,episode_uri,audiobook,chapter,"
        "platform,country,reason_start,reason_end,shuffle,skipped,offline,incognito,"
        "device,is_skip")
INSERT = (f"INSERT OR IGNORE INTO plays ({COLS}) VALUES ("
          + ",".join("?" * len(COLS.split(","))) + ")")


def local_parts(d):
    """UTC datetime -> (hour, weekday, YYYY-MM-DD) in the home timezone."""
    loc = d.replace(tzinfo=UTC).astimezone(HOME_TZ)
    return loc.hour, loc.weekday(), loc.strftime("%Y-%m-%d")


def row_tuple(who, r):
    ts = r.get("ts")
    d = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    lh, ldow, lday = local_parts(d)
    # Spotify's own `skipped` field is unusable on its own: it is null for whole
    # years (nothing at all for 2017-2021) and int(bool(None)) is 0, so absent
    # telemetry is indistinguishable from "played to the end". reason_end is
    # populated across all sixteen years, so a forward-button press is the one
    # skip signal that means the same thing in 2013 and 2026.
    return (
        who, ts, d.year, d.month, ts[:10], d.hour, d.weekday(),
        lh, ldow, lday,
        r.get("ms_played") or 0, kind_of(r),
        r.get("master_metadata_track_name"),
        r.get("master_metadata_album_artist_name"),
        r.get("master_metadata_album_album_name"),
        r.get("spotify_track_uri"),
        norm_title(r.get("master_metadata_track_name")),
        norm_artist(r.get("master_metadata_album_artist_name")),
        r.get("episode_name"), r.get("episode_show_name"), r.get("spotify_episode_uri"),
        r.get("audiobook_title"), r.get("audiobook_chapter_title"),
        r.get("platform"), r.get("conn_country"),
        r.get("reason_start"), r.get("reason_end"),
        int(bool(r.get("shuffle"))), int(bool(r.get("skipped"))),
        int(bool(r.get("offline"))), int(bool(r.get("incognito_mode"))),
        devices.device_of(r.get("platform")),
        int(r.get("reason_end") == "fwdbtn"),
    )


def migrate(con):
    """Add derived columns a previously-loaded database predates, and fill them.

    Everything here is computed from columns already stored, so there is no need
    to re-read the exports -- and re-reading would be the wrong instinct anyway,
    since backfilling 299k rows takes a second and a full rebuild takes minutes.
    New columns land in SCHEMA for a fresh load and here for one that exists.

    The test for "already done" is whether the values are present, not whether
    the column is. ALTER TABLE commits immediately in SQLite, so a run that dies
    midway leaves the columns in place and entirely NULL -- and a migration keyed
    on the schema would then skip the backfill forever and report success.
    """
    have = {r[1] for r in con.execute("PRAGMA table_info(plays)")}
    for col, ddl in (("hour_local", "INTEGER"), ("dow_local", "INTEGER"),
                     ("day_local", "TEXT"), ("device", "TEXT"),
                     ("is_skip", "INTEGER")):
        if col not in have:
            con.execute(f"ALTER TABLE plays ADD COLUMN {col} {ddl}")

    def unfilled(col):
        return con.execute(
            f"SELECT COUNT(*) FROM plays WHERE {col} IS NULL").fetchone()[0]

    if unfilled("hour_local"):
        rows = con.execute(
            "SELECT id, ts FROM plays WHERE hour_local IS NULL").fetchall()
        con.executemany(
            "UPDATE plays SET hour_local=?, dow_local=?, day_local=? WHERE id=?",
            [(*local_parts(datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")), i)
             for i, ts in rows])
        print(f"  migrated: local time for {len(rows):,} plays")

    if unfilled("device"):
        con.executemany(
            "UPDATE plays SET device=? WHERE platform IS ? AND device IS NULL",
            [(devices.device_of(p), p) for (p,) in
             con.execute("SELECT DISTINCT platform FROM plays")])
        print("  migrated: device for every platform string")

    if unfilled("is_skip"):
        con.execute(
            "UPDATE plays SET is_skip = (reason_end = 'fwdbtn') WHERE is_skip IS NULL")
        n = con.execute("SELECT SUM(is_skip) FROM plays").fetchone()[0]
        print(f"  migrated: {n:,} forward-button skips")

    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?")
    ap.add_argument("--rebuild", action="store_true", help="wipe and reload from scratch")
    a = ap.parse_args()

    if a.rebuild and os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    con.executescript(TABLES_TAIL)
    migrate(con)
    con.executescript(INDEXES)

    before = con.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
    seen = {r[0] for r in con.execute("SELECT sha1 FROM sources")}
    roots = discover(a.export)
    if not roots:
        print("No exports found. Drop the folder or .zip in ~/Downloads or "
              f"{EXPORTS} and run again.")
        return

    t0 = time.time()
    added_total = skipped_files = 0
    for root in roots:
        print(f"\n{os.path.basename(root)}")
        for folder, who in sorted(profiles(root).items()):
            files = sorted(glob.glob(os.path.join(root, folder, "Streaming_History_*.json")))
            per_person = 0
            for f in files:
                digest = sha1(f)
                if digest in seen:
                    skipped_files += 1
                    continue
                with open(f) as fh:
                    try:
                        rows = json.load(fh)
                    except json.JSONDecodeError as e:
                        print(f"    !! skipping unreadable {os.path.basename(f)}: {e}")
                        continue
                batch = [row_tuple(who, r) for r in rows if r.get("ts")]
                n0 = con.total_changes
                con.executemany(INSERT, batch)
                added = con.total_changes - n0
                ts_all = [b[1] for b in batch]
                con.execute(
                    "INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?,?)",
                    (digest, os.path.basename(root), os.path.basename(f), who,
                     len(batch), added, min(ts_all) if ts_all else None,
                     max(ts_all) if ts_all else None,
                     datetime.datetime.now().isoformat(timespec="seconds")))
                seen.add(digest)
                per_person += added
                added_total += added
            if files:
                print(f"  {who:<6} +{per_person:>7,} new  ({len(files)} files)")
        con.commit()

    con.execute("ANALYZE")
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
    rng = con.execute("SELECT MIN(ts), MAX(ts) FROM plays").fetchone()
    con.close()

    print(f"\n+{added_total:,} new plays"
          + (f" ({skipped_files} files already ingested)" if skipped_files else ""))
    print(f"{total:,} plays total (was {before:,}) covering {rng[0][:10]} -> {rng[1][:10]}")
    print(f"{DB} ({os.path.getsize(DB)/1e6:.0f} MB) in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
