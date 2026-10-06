#!/usr/bin/env python3
"""Merge the measured phases with the written copy into web/story.json.

Two files feed this. phases.json is measured -- boundaries scored from the data,
every figure recomputed with the children and the overnight speaker removed.
story_copy.json is written by hand, because what a period felt like is not a
column in a database.

The risk in that split is drift: copy that quotes a number, data that later
moves, and a page that states something no longer true with complete confidence.
So every number appearing in the prose is checked against the numbers the phase
actually holds, and anything unaccounted for is listed. A figure that comes from
outside phases.json is fine, but it has to be declared in VERIFIED below, with
where it came from, rather than passing silently.

    python3 story_data.py
"""
import json
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
PHASES = os.path.join(HERE, "web", "phases.json")
COPY = os.path.join(HERE, "story_copy.json")
OUT = os.path.join(HERE, "web", "story.json")
DB = os.path.join(HERE, "web", "listening.db")
SEP = "\x1f"
MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


def pictures(db, lo, hi, artist=None):
    """What was playing in a window, with the pictures the page needs.

    Resolved here rather than in the browser so the Seasons page stays a
    single file with no database to fetch. His listening only: the children
    and the overnight speaker are out, as everywhere else on the site.
    """
    art = {}
    for k, key, url in db.execute("SELECT kind, key, url FROM art"):
        art[(k, key)] = url
    w = "p.day BETWEEN ? AND ? AND p.is_kid = 0 AND p.is_ambient = 0"
    args = [lo, hi]
    if artist:
        w += " AND a.name = ?"; args.append(artist)
    # The track's Spotify address rides along, for the clip a tap plays.
    tracks = [[t, a, n, art.get(("t", t + SEP + a)) or art.get(("l", (al or "") + SEP + a)), uri]
              for t, a, al, n, uri in db.execute(f"""
        SELECT t.name, a.name, t.album, COUNT(*) n, MAX(t.uri) FROM plays p
        JOIN tracks t ON t.id = p.track_id JOIN artists a ON a.id = t.artist_id
        WHERE {w} GROUP BY t.id ORDER BY n DESC LIMIT 5""", args)]
    artists = [[a, n, art.get(("a", a))] for a, n in db.execute(f"""
        SELECT a.name, COUNT(*) n FROM plays p JOIN artists a ON a.id = p.artist_id
        WHERE {w} GROUP BY a.id ORDER BY n DESC LIMIT 6""", args)]
    shows = [] if artist else [[s, n, art.get(("p", s))] for s, n in db.execute(f"""
        SELECT s.name, COUNT(*) n FROM plays p JOIN shows s ON s.id = p.show_id
        WHERE p.day BETWEEN ? AND ? AND p.is_kid = 0 AND p.is_ambient = 0
        GROUP BY s.id ORDER BY n DESC LIMIT 3""", [lo, hi]) if n >= 10]
    # The collage: the most-played records of the window, one cover each.
    covers = []
    for al, a, n in db.execute(f"""
        SELECT t.album, a.name, COUNT(*) n FROM plays p
        JOIN tracks t ON t.id = p.track_id JOIN artists a ON a.id = t.artist_id
        WHERE {w} AND t.album IS NOT NULL GROUP BY t.album, a.id
        ORDER BY n DESC LIMIT 40""", args):
        url = art.get(("l", al + SEP + a))
        # With the album and artist, so a cover can link to its own page.
        if url and url not in [c[0] for c in covers]:
            covers.append([url, al, a])
        if len(covers) == 9:
            break
    return {"tracks": tracks, "artists": artists, "shows": shows, "covers": covers}

MONTH = ["January","February","March","April","May","June",
         "July","August","September","October","November","December"]

# Numbers quoted in the prose that are true but are not fields of phases.json.
# Each one has been checked against listening.db by hand; the query is the note.
# story_copy.json may carry them per season under "verified": {"874": "note"}.
VERIFIED = {}


def known_numbers(p):
    """Every figure this phase legitimately supports, in the forms prose uses."""
    vals = {p["plays"], round(p["hours"]), round(p["music_h_per_mo"]),
            round(p["podcast_h_per_mo"]), round(p["new_pct"]), round(p["top5_pct"]),
            p["artists"], round(p["per_1k"]), p["release_median"], p["peak_hour"],
            round(p["small_hours_pct"]), int(p["from"][:4]), int(p["to"][:4])}
    vals |= {n for _, n in p["top"]}
    vals |= {round(d) for d in p["decades"]}
    vals |= {p["repeated"]["plays"]}
    for row in p.get("away") or []:
        vals |= {row[1], row[2]}
    for row in p.get("defining") or []:
        vals.add(round(row[1]))
    return {v for v in vals if v is not None}


# A bullet is one line on a phone. At 15px in the hero's width that is about
# 34 characters, so anything longer fails the build rather than wrapping.
BULLET_MAX = 34


# A title is one line on a phone at the hero's size: 24 characters.
TITLE_MAX = 24


def tribute(db, c):
    """The bonus page for one artist: where they ranked each year, against the best of
    everyone else, and the medley's tracks with their covers."""
    who = c["artist"]
    rows = db.execute("""
        SELECT substr(p.day,1,4) yr, a.name, COUNT(*) n FROM plays p
        JOIN artists a ON a.id = p.artist_id
        WHERE p.is_kid = 0 AND p.is_ambient = 0 AND p.kind = 'track'
        GROUP BY 1, 2""").fetchall()
    by = {}
    for yr, name, n in rows:
        by.setdefault(yr, []).append((n, name))
    years = []
    for yr in sorted(by):
        ranked = sorted(by[yr], reverse=True)
        mine = next(((i + 1, n) for i, (n, nm) in enumerate(ranked) if nm == who), (None, 0))
        other = next((x for x in ranked if x[1] != who), (0, ""))
        years.append([int(yr), mine[1], mine[0], other[1], other[0]])
    art = {(k, key): url for k, key, url in db.execute("SELECT kind, key, url FROM art")}
    medley = []
    for t in c.get("medley", []):
        r = db.execute("""SELECT t.name, t.uri, t.album, COUNT(*) n FROM plays p
                          JOIN tracks t ON t.id = p.track_id JOIN artists a ON a.id = t.artist_id
                          WHERE a.name = ? AND t.name = ? AND p.is_kid = 0 AND p.is_ambient = 0
                          GROUP BY t.id ORDER BY n DESC LIMIT 1""", (who, t)).fetchone()
        if r:
            medley.append([r[0], r[1], r[3], art.get(("t", r[0] + SEP + who))
                           or art.get(("l", (r[2] or "") + SEP + who))])
    return {"years": years, "medley": medley, "bonus": True, "artist": who}


PREVIEWS = os.path.join(HERE, "preview_cache.json")


def preview(uri, cache):
    """Spotify's own 30-second clip for a track, as a plain mp3 address.

    The embedded Spotify player cannot be started from outside itself on a
    phone, so the page plays the clip in its own audio element instead. The
    address is read once from the track's embed page and kept; a miss is not
    stored, so it is asked again on the next build.
    """
    if not uri or ":track:" not in uri:
        return None
    if uri not in cache:
        import requests
        try:
            html = requests.get(
                "https://open.spotify.com/embed/track/" + uri.split(":")[-1], timeout=30,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}).text
            m = re.search(r'"audioPreview":\{"url":"([^"]+)"', html)
            if m:
                cache[uri] = m.group(1)
        except Exception as e:
            print(f"  no preview for {uri}: {e}")
    return cache.get(uri)


def thumb(uri, cache):
    """A cover for a track the art cache does not hold (the children's songs
    were never fetched), from Spotify's public oEmbed."""
    if not uri:
        return None
    k = uri + "#img"
    if k not in cache:
        import requests
        try:
            cache[k] = requests.get("https://open.spotify.com/oembed", timeout=30,
                                    params={"url": uri}).json().get("thumbnail_url")
        except Exception:
            return None
    return cache.get(k)


def depth_url(pid):
    """The picture's near-and-far map (season_depth.py), if it has one."""
    f = os.path.join(HERE, "web", "seasons", f"{pid}.d.jpg")
    return f"seasons/{pid}.d.jpg?v={int(os.path.getmtime(f))}" if os.path.exists(f) else None


def art_url(pid):
    """The picture's path with its file time on it, so a regenerated picture
    is fetched fresh instead of served from a browser's cache."""
    f = os.path.join(HERE, "web", "seasons", f"{pid}.jpg")
    if not os.path.exists(f):
        return None
    return f"seasons/{pid}.jpg?v={int(os.path.getmtime(f))}"


def check_bullets(copy):
    long = []
    for pid, c in copy.items():
        if isinstance(c, dict) and len(c.get("title", "")) > TITLE_MAX:
            long.append((pid, len(c["title"]), c["title"] + "  <- title"))
    for pid, c in copy.items():
        for b in (c.get("bullets") or []) if isinstance(c, dict) else []:
            text = re.sub(r"<[^>]+>", "", b)
            if len(text) > BULLET_MAX:
                long.append((pid, len(text), text))
            if text.rstrip().endswith("."):
                long.append((pid, len(text), text + "  <- no full stop"))
    # Four, always: each of the last three arrives with one of the page's three
    # sections, so a season with three would leave a section arriving alone.
    for pid, c in copy.items():
        if isinstance(c, dict) and "bullets" in c and len(c["bullets"]) != 4:
            raise SystemExit(f"  {pid} has {len(c['bullets'])} bullets; every season carries four")
    if long:
        for pid, n, t in long:
            print(f"    {pid}  {n} chars  {t}")
        raise SystemExit(f"  bullets must fit one line: {BULLET_MAX} characters, no full stop")


def main():
    if not os.path.exists(PHASES) or not os.path.exists(COPY):
        print("no seasons, so no Seasons page")
        if os.path.exists(OUT):
            os.remove(OUT)
        return
    phases = {p["id"]: p for p in json.load(open(PHASES))}
    copy = json.load(open(COPY))
    copy.pop("_note", None)
    check_bullets(copy)
    order = [p["id"] for p in json.load(open(PHASES))]

    db = sqlite3.connect(DB)
    hr = lambda h: f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"
    unmatched = []
    out = []
    for pid, c in copy.items():
        if isinstance(c, dict) and c.get("standalone"):
            out_extra = dict(c); out_extra["id"] = pid
            order = order + [pid]
            phases[pid] = None

    for pid in order:
        if phases.get(pid) is None:
            c = copy[pid]
            out.append({"id": pid, "title": c["title"], "accent": c["accent"],
                        "when": c["when"], "body": c["body"], "soft": c["soft"],
                        "who": c["who"], "whoLabel": c.get("who_label"),
                        "whoUnit": c.get("who_unit"), "track": c["track"],
                        "tape": c["tape"], "vin": c["vin"], "median": c["median"],
                        "bullets": c.get("bullets", []), "span": c.get("span"),
                        "art": art_url(pid), "depth": depth_url(pid),
                        **pictures(db, c["range"][0], c["range"][1], c.get("artist")),
                        **(tribute(db, c) if c.get("bonus") else {})})
            if c.get("bonus"):
                # The bonus page's totals are filled in from the data, so a new
                # export moves them without anyone editing the copy.
                o, who = out[-1], c["artist"]
                mine = "p.is_kid = 0 AND p.is_ambient = 0"
                top = db.execute(f"""SELECT a.name, COUNT(*) n FROM plays p
                    JOIN artists a ON a.id = p.artist_id WHERE {mine}
                    GROUP BY a.id ORDER BY n DESC LIMIT 2""").fetchall()
                n = dict(top).get(who, 0)
                nxt = next((v for k, v in top if k != who), 1)
                songs, hours = db.execute(f"""SELECT COUNT(DISTINCT t.name), SUM(p.ms) / 3.6e6
                    FROM plays p JOIN tracks t ON t.id = p.track_id
                    JOIN artists a ON a.id = t.artist_id
                    WHERE {mine} AND a.name = ?""", [who]).fetchone()
                o["bullets"] = [b.replace("{n}", f"{n:,}").replace("{x}", str(round(n / nxt)))
                                 .replace("{s}", f"{songs:,}").replace("{h}", f"{round(hours):,}")
                                for b in o["bullets"]]
                o["span"] = dict(o["span"], y1=max(o["span"]["y1"],
                                                    max(x["span"]["y1"] for x in out[:-1])))
            continue
        p, c = phases[pid], copy.get(pid)
        if not c:
            raise SystemExit(f"{pid} has no copy written")

        c.setdefault("body", []); c.setdefault("soft", "")
        c.setdefault("accent", "#8fa6c9")
        prose = " ".join(c["body"] + c.get("bullets", [])) + " " + c["soft"]
        prose = re.sub(r"<i>.*?</i>", " ", prose)      # album titles: 1989, Midnights
        prose = re.sub(r"<[^>]+>", "", prose)
        cited = {int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", prose)}
        cited = {n for n in cited if not (1900 <= n <= 2100)}   # years are references
        allowed = known_numbers(p) | {int(k) for k in (c.get("verified") or {})}
        for n in sorted(cited - allowed):
            unmatched.append((pid, n))

        y0, m0 = int(p["from"][:4]), int(p["from"][5:7])
        y1, m1 = int(p["to"][:4]), int(p["to"][5:7])
        out.append({
            "id": pid,
            "title": c["title"],
            "accent": c["accent"],
            "when": f"{MONTH[m0-1]} {y0} — {MONTH[m1-1]} {y1}",
            "body": c["body"],
            "soft": c["soft"],
            "who": [[a, n] for a, n in p["top"][:7]],
            # The copy can name the song a season plays; otherwise it is the
            # measured signature track.
            "track": c.get("track") or {"t": p["signature"]["track"], "a": p["signature"]["artist"],
                      "uri": p["signature"]["uri"],
                      "n": p["repeated"]["plays"] if
                           p["signature"]["track"] == p["repeated"]["track"] else None},
            # phases.json stores device as "iOS 46%"; "Mostly iOS 46%" reads
            # like a broken sentence, so the share goes in its own clause.
            "tape": (f"Mostly <b>{p['device'].split()[0]}</b>"
                     f"{' (' + p['device'].split()[1] + ')' if len(p['device'].split()) > 1 else ''}"
                     f" · peak hour "
                     f"<b>{p['peak_hour']:02d}:00</b> · median record "
                     f"<b>{p['release_median']}</b>"),
            "vin": [round(d, 1) for d in p["decades"]],
            "median": p["release_median"],
            # Eight of the twelve boundaries were scored strong or moderate
            # against the six months either side. Four were not: the listening
            # changes character there but the change point does not stand out,
            # so the line is drawn by judgement. The page says which is which,
            # because a reader cannot tell a measured boundary from a chosen
            # one by looking at it.
            "editorial": p["boundary"] == "weak-editorial",
            "bullets": c.get("bullets", []),
            "span": {"from": f"{MON[m0-1]} {y0}", "to": f"{MON[m1-1]} {y1}",
                     "months": p["months"], "y0": y0 + (m0 - 1) / 12,
                     "y1": y1 + m1 / 12},
            "stats": [[f"{p['artists']:,}", "artists"],
                      [hr(p["peak_hour"]), "peak hour"],
                      [p["device"].split()[0].replace("iOS", "iPhone"), "mostly on"],
                      [str(p["release_median"]), "median record"]] +
                     ([[f"{round(p['podcast_h_per_mo'])}h", "podcasts a month"]]
                      if p["podcast_h_per_mo"] >= 1 else []),
            "art": art_url(pid), "depth": depth_url(pid),
            **pictures(db, p["from"] + "-01", p["to"] + "-31"),
        })

    # The signature track's own cover, so it is never borrowed from the collage.
    art = {(k, key): url for k, key, url in db.execute("SELECT kind, key, url FROM art")}
    previews = json.load(open(PREVIEWS)) if os.path.exists(PREVIEWS) else {}
    for o in out:
        t = o.get("track") or {}
        o["track"]["cover"] = (art.get(("t", f"{t.get('t')}{SEP}{t.get('a')}"))
                               or art.get(("p", t.get("a")))   # a podcast's show
                               or thumb(t.get("uri"), previews))
        # What the page plays: a clip the copy names (a podcast's own file),
        # or Spotify's preview of the track.
        o["track"]["audio"] = t.get("audio") or preview(t.get("uri"), previews)
        for m in o.get("medley") or []:
            m.append(preview(m[1], previews))
        # Each top song carries its own clip, so tapping its cover plays it.
        for tr in o.get("tracks") or []:
            tr.append(preview(tr[4], previews))
        if not o["track"]["audio"]:
            print(f"  {o['id']} has nothing to play")
    json.dump(previews, open(PREVIEWS, "w"), indent=1)

    if unmatched:
        print("  numbers in the prose with nothing behind them:")
        for pid, n in unmatched:
            print(f"    {pid}  {n:,}")
        raise SystemExit('  fix the copy, or vouch for a number in story_copy.json: "verified": {"874": "where it comes from"}')

    json.dump({"phases": out}, open(OUT, "w"), separators=(",", ":"), ensure_ascii=False)
    print(f"web/story.json  {os.path.getsize(OUT)/1e3:.0f} KB  {len(out)} phases")
    print("  every number in the prose accounted for")


if __name__ == "__main__":
    main()
