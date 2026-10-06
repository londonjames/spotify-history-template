#!/usr/bin/env python3
"""Query 15 years of Spotify history.

  ./sh.py song "baby beluga"      year-by-year distribution for a track
  ./sh.py artist "taylor swift"   year-by-year for an artist
  ./sh.py show "rewatchables"     year-by-year for a podcast
  ./sh.py top --year 2026         top tracks/artists in a period
  ./sh.py year 2018               a year in review
  ./sh.py stats                   overall summary
  ./sh.py sql "SELECT ..."        raw query
Options: --who Me --from 2011 --to 2026 --by plays|hours --limit N
"""
import argparse, os, sqlite3, sys, textwrap

import config

ME = config.NAME.replace("'", "''")

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spotify.db")
YEARS = list(range(2011, 2027))


def kids_predicate():
    """SQL fragment + params matching everything tagged in kids.txt."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_l", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "load.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kids.txt")
    arts, trks, albums = [], [], []
    if os.path.exists(path):
        for line in open(path):
            line = line.split("#")[0].strip()
            if not line: continue
            if line.startswith("~"):
                albums.append(line[1:].strip().lower())
            elif "::" in line:
                a, t = line.split("::", 1)
                trks.append((m.norm_artist(a.strip()), m.norm_title(t.strip())))
            else:
                arts.append(m.norm_artist(line))
    parts, params = [], []
    if arts:
        parts.append("artist_norm IN (%s)" % ",".join("?" * len(arts))); params += arts
        # Show names come from Spotify with curly apostrophes and stray trailing
        # spaces ("Mr Charlton’s Audio Stories "), so both sides are folded.
        parts.append("REPLACE(LOWER(TRIM(show)), '’', '''') IN (%s)"
                     % ",".join("?" * len(arts)))
        params += [a.replace("’", "'") for a in arts]
    for a, t in trks:
        parts.append("(artist_norm = ? AND track_norm = ?)"); params += [a, t]
    # Match the web export's unit: a recording that ever appeared on a kids
    # album is the kids', even on the plays filed under a different album.
    for al in albums:
        parts.append("(track_norm, artist_norm) IN "
                     "(SELECT track_norm, artist_norm FROM plays "
                     " WHERE LOWER(album) LIKE ? AND track_norm IS NOT NULL)")
        params.append(al + "%")
    return ("(" + " OR ".join(parts) + ")" if parts else "0"), params


def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def bar(v, mx, width=38):
    if mx <= 0: return ""
    n = int(round(v / mx * width))
    return "█" * n + ("▏" if v > 0 and n == 0 else "")

def hrs(ms): return ms / 3.6e6

def fmt_dur(ms):
    h = hrs(ms)
    if h >= 1: return f"{h:,.1f}h"
    return f"{ms/60000:.0f}m"

def distribution(rows, label, sub, by):
    """rows: list of (year, plays, ms). Print a year histogram."""
    d = {r[0]: (r[1], r[2]) for r in rows}
    tot_p = sum(v[0] for v in d.values())
    tot_m = sum(v[1] for v in d.values())
    if not tot_p:
        print("no plays found"); return
    print(f"\n\033[1m{label}\033[0m")
    if sub: print(f"{sub}")
    print(f"{tot_p:,} plays · {fmt_dur(tot_m)} total\n")
    vals = {y: (d.get(y, (0, 0))[0] if by == "plays" else hrs(d.get(y, (0, 0))[1])) for y in YEARS}
    mx = max(vals.values()) or 1
    first = min((y for y in YEARS if vals[y] > 0), default=YEARS[0])
    last = max((y for y in YEARS if vals[y] > 0), default=YEARS[-1])
    unit = "plays" if by == "plays" else "hours"
    for y in YEARS:
        if y < first or y > last: continue
        v = vals[y]
        n = f"{v:,.0f}" if by == "plays" else f"{v:,.1f}"
        mark = "\033[2m" if v == 0 else ""
        end = "\033[0m" if v == 0 else ""
        print(f"  {mark}{y}  {n:>7}  {bar(v, mx)}{end}")
    peak = max(YEARS, key=lambda y: vals[y])
    print(f"\n  peak {peak} ({vals[peak]:,.0f} {unit}) · first {first} · last {last}")

def where(a, extra="", params=()):
    w, p = ["1=1"], []
    if getattr(a, "no_kids", False):
        pred, kp = kids_predicate()
        # COALESCE: on music rows LOWER(show) IN (...) is NULL, and NOT NULL drops the row.
        w.append("NOT COALESCE(" + pred + ", 0)"); p.extend(kp)
    if a.who:  w.append("who = ?");  p.append(a.who)
    if a.frm:  w.append("year >= ?"); p.append(a.frm)
    if a.to:   w.append("year <= ?"); p.append(a.to)
    if extra:  w.append(extra); p.extend(params)
    return " AND ".join(w), p

def pick(c, a, field, term, kind):
    """Resolve a fuzzy term to the best matching entity, preferring exact."""
    w, p = where(a, "kind = ?", (kind,))
    q = f"""SELECT {field} AS name, COUNT(*) n, SUM(ms_played) ms
            FROM plays WHERE {w} AND {field} IS NOT NULL
              AND LOWER({field}) LIKE ? GROUP BY LOWER({field})
            ORDER BY (LOWER({field}) = ?) DESC, n DESC LIMIT 12"""
    rows = c.execute(q, (*p, f"%{term.lower()}%", term.lower())).fetchall()
    return rows

def cmd_song(c, a):
    """Resolve to one recording, merging edition variants (remasters etc.)."""
    w, p = where(a, "kind='track' AND track_norm IS NOT NULL AND track_norm LIKE ?",
                 (f"%{a.term.lower()}%",))
    if a.artist:
        w += " AND artist_norm LIKE ?"; p.append(f"%{a.artist.lower()}%")
    cands = c.execute(f"""SELECT track_norm, artist_norm,
                                 MIN(track) title, MIN(artist) art,
                                 COUNT(*) n, SUM(ms_played) ms,
                                 COUNT(DISTINCT track) variants
                          FROM plays WHERE {w}
                          GROUP BY track_norm, artist_norm
                          ORDER BY (track_norm = ?) DESC, n DESC LIMIT 25""",
                      (*p, a.term.lower())).fetchall()
    if not cands:
        print(f"no track matching {a.term!r}"); return
    if len(cands) > 1:
        print(f"\n\033[1m{len(cands)} recordings match {a.term!r}\033[0m  "
              f"\033[2m(showing #1 — narrow with --artist)\033[0m")
        for i, r in enumerate(cands[:10], 1):
            m = "\033[1m" if i == 1 else "\033[2m"
            print(f"  {m}{i}. {r['n']:>5} plays  {r['title'][:42]:<42} — {r['art']}\033[0m")
    top = cands[0]
    w, p = where(a, "track_norm = ? AND artist_norm = ? AND kind='track'",
                 (top["track_norm"], top["artist_norm"]))
    ydist = c.execute(f"SELECT year, COUNT(*), SUM(ms_played) FROM plays WHERE {w} GROUP BY year", p).fetchall()
    meta = c.execute(f"SELECT album, COUNT(*) n FROM plays WHERE {w} GROUP BY album ORDER BY n DESC LIMIT 1", p).fetchone()
    distribution(ydist, top["title"], f"{top['art']} · {meta['album']}" if meta else top["art"], a.by)
    if top["variants"] > 1:
        names = [r[0] for r in c.execute(
            f"SELECT track, COUNT(*) n FROM plays WHERE {w} GROUP BY track ORDER BY n DESC", p)]
        print(f"  \033[2mmerged {top['variants']} editions: {', '.join(names[:4])}\033[0m")
    x = c.execute(f"""SELECT MIN(ts) f, MAX(ts) l, SUM(skipped)*1.0/COUNT(*) sk,
                      SUM(shuffle)*1.0/COUNT(*) sh, AVG(ms_played) avg FROM plays WHERE {w}""", p).fetchone()
    print(f"  first heard {x['f'][:10]} · last {x['l'][:10]} · skipped {x['sk']*100:.0f}%"
          f" · shuffled {x['sh']*100:.0f}% · avg listen {x['avg']/1000:.0f}s")

def cmd_artist(c, a):
    rows = pick(c, a, "artist", a.term, "track")
    if not rows:
        print(f"no artist matching {a.term!r}"); return
    name = rows[0]["name"]
    w, p = where(a, "artist = ? AND kind='track'", (name,))
    ydist = c.execute(f"SELECT year, COUNT(*), SUM(ms_played) FROM plays WHERE {w} GROUP BY year", p).fetchall()
    nt = c.execute(f"SELECT COUNT(DISTINCT track_uri) n FROM plays WHERE {w}", p).fetchone()["n"]
    distribution(ydist, name, f"{nt:,} distinct tracks", a.by)
    print("\n  top tracks:")
    for r in c.execute(f"""SELECT MIN(track) track, COUNT(*) n, SUM(ms_played) ms FROM plays WHERE {w}
                           GROUP BY track_norm ORDER BY n DESC LIMIT {a.limit}""", p):
        print(f"    {r['n']:>5}  {r['track']}  \033[2m{fmt_dur(r['ms'])}\033[0m")

def cmd_show(c, a):
    rows = pick(c, a, "show", a.term, "podcast")
    if not rows:
        print(f"no podcast matching {a.term!r}"); return
    name = rows[0]["name"]
    w, p = where(a, "show = ? AND kind='podcast'", (name,))
    ydist = c.execute(f"SELECT year, COUNT(*), SUM(ms_played) FROM plays WHERE {w} GROUP BY year", p).fetchall()
    ne = c.execute(f"SELECT COUNT(DISTINCT episode) n FROM plays WHERE {w}", p).fetchone()["n"]
    distribution(ydist, name, f"{ne:,} distinct episodes", a.by or "hours")
    print("\n  most-played episodes:")
    for r in c.execute(f"""SELECT episode, SUM(ms_played) ms FROM plays WHERE {w}
                           GROUP BY episode ORDER BY ms DESC LIMIT {a.limit}""", p):
        print(f"    {fmt_dur(r['ms']):>7}  {(r['episode'] or '')[:70]}")

def cmd_top(c, a):
    order = "n" if a.by == "plays" else "ms"
    for field, kind, title in (("artist", "track", "artists"), ("track", "track", "tracks"), ("show", "podcast", "podcasts")):
        w, p = where(a, "kind = ? AND " + field + " IS NOT NULL", (kind,))
        print(f"\n\033[1mtop {title}\033[0m")
        for i, r in enumerate(c.execute(
                f"""SELECT {field} name, COUNT(*) n, SUM(ms_played) ms FROM plays
                    WHERE {w} GROUP BY {field} ORDER BY {order} DESC LIMIT {a.limit}""", p), 1):
            print(f"  {i:>2}. {r['name'][:52]:<52} {r['n']:>6} plays  {fmt_dur(r['ms']):>8}")

def cmd_year(c, a):
    a.frm = a.to = int(a.term)
    w, p = where(a)
    s = c.execute(f"""SELECT COUNT(*) n, SUM(ms_played) ms,
                      COUNT(DISTINCT track_uri) t, COUNT(DISTINCT artist) ar
                      FROM plays WHERE {w}""", p).fetchone()
    print(f"\n\033[1m{a.term}\033[0m — {s['n']:,} plays · {fmt_dur(s['ms'])} · "
          f"{s['t']:,} tracks · {s['ar']:,} artists")
    cmd_top(c, a)
    print("\n\033[1mby month\033[0m")
    rows = c.execute(f"SELECT month, SUM(ms_played) ms FROM plays WHERE {w} GROUP BY month", p).fetchall()
    d = {r["month"]: r["ms"] for r in rows}
    mx = max(d.values()) if d else 1
    for m in range(1, 13):
        v = d.get(m, 0)
        print(f"  {m:>2}  {hrs(v):>6.0f}h  {bar(v, mx, 30)}")

def cmd_stats(c, a):
    w, p = where(a)
    s = c.execute(f"""SELECT COUNT(*) n, SUM(ms_played) ms, MIN(ts) f, MAX(ts) l,
                      COUNT(DISTINCT track_uri) t, COUNT(DISTINCT artist) ar,
                      COUNT(DISTINCT show) sh FROM plays WHERE {w}""", p).fetchone()
    print(f"\n\033[1m{s['f'][:10]} → {s['l'][:10]}\033[0m")
    print(f"  {s['n']:,} plays · {hrs(s['ms']):,.0f} hours ({hrs(s['ms'])/24:,.0f} days)")
    print(f"  {s['t']:,} tracks · {s['ar']:,} artists · {s['sh']:,} podcasts")
    rows = c.execute(f"SELECT year, COUNT(*) n, SUM(ms_played) ms FROM plays WHERE {w} GROUP BY year", p).fetchall()
    distribution([(r["year"], r["n"], r["ms"]) for r in rows], "listening by year", "", a.by)

def cmd_sql(c, a):
    for r in c.execute(a.term):
        print(" | ".join(str(x) for x in r))

def cmd_kids(c, a):
    pred, kp = kids_predicate()
    tot = c.execute(f"SELECT COUNT(*) n, SUM(ms_played) ms FROM plays WHERE who='{ME}'").fetchone()
    hit = c.execute(f"SELECT COUNT(*) n, SUM(ms_played) ms FROM plays WHERE who='{ME}' AND {pred}", kp).fetchone()
    print(f"\n\033[1mkids.txt catches\033[0m {hit['n']:,} of {tot['n']:,} plays "
          f"({100*hit['n']/tot['n']:.1f}%) · {hrs(hit['ms'] or 0):,.0f} of {hrs(tot['ms']):,.0f} hours")
    print("\n  what it catches, by artist:")
    for r in c.execute(f"""SELECT artist, COUNT(*) n, SUM(ms_played) ms, MIN(year) y0, MAX(year) y1
                           FROM plays WHERE who='{ME}' AND {pred}
                           GROUP BY artist_norm ORDER BY ms DESC LIMIT {a.limit}""", kp):
        print(f"    {hrs(r['ms']):>6.0f}h  {r['n']:>6,} plays  {r['y0']}-{r['y1']}  {r['artist']}")
    print("\n  by year, share of your listening:")
    rows = c.execute(f"""SELECT year, SUM(ms_played) ms,
                         SUM(CASE WHEN {pred} THEN ms_played ELSE 0 END) kid
                         FROM plays WHERE who='{ME}' GROUP BY year""", kp).fetchall()
    for r in rows:
        pct = 100 * (r["kid"] or 0) / r["ms"] if r["ms"] else 0
        print(f"    {r['year']}  {pct:>4.0f}%  {bar(pct, 100, 30)}")
    print("\n  edit kids.txt to change this, then re-run ./refresh")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["song", "artist", "show", "top", "year", "stats", "sql", "kids"])
    ap.add_argument("term", nargs="?", default="")
    ap.add_argument("--who", default=None)
    ap.add_argument("--from", dest="frm", type=int)
    ap.add_argument("--to", type=int)
    ap.add_argument("--year", type=int)
    ap.add_argument("--by", choices=["plays", "hours"], default="plays")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--artist", default=None, help="narrow a song search to one artist")
    ap.add_argument("--no-kids", dest="no_kids", action="store_true",
                    help="exclude everything tagged in kids.txt")
    a = ap.parse_args()
    if a.year: a.frm = a.to = a.year
    c = con()
    {"song": cmd_song, "artist": cmd_artist, "show": cmd_show, "top": cmd_top,
     "year": cmd_year, "stats": cmd_stats, "sql": cmd_sql,
     "kids": cmd_kids}[a.cmd](c, a)
    print()

if __name__ == "__main__":
    main()
