#!/usr/bin/env python3
"""Stage everything Vercel serves into site/.

Two kinds of thing end up there and they arrive differently:

  the pages      built HTML with their first-paint payload already inlined
  listening.db   fetched by the page after first paint, not inlined

Nothing here runs on a server. There was a serverless function that wrote SQL
from an English question through the API; the questions are written out and
answered in the browser now, so the deployment is static files only.

Locally the pages are listening-*.html so they read clearly in Finder; on the
web the entry point has to be index.html, so each one is renamed on the way
into site/. Nothing inside them is rewritten: the templates link to the served
paths directly ("/", "/story.html").
"""
import json
import os
import shutil
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
SITE = os.path.join(HERE, "site")

PAGES = [
    ("listening-history.html", "index.html"),
    ("listening-story.html", "story.html"),
]

# /a/Some+Artist is what gets copied and shared; it is served by the function
# so the tags are right, and the function sends a person on to the hash the page
# actually runs on. The hash URLs keep working untouched.
REWRITES = [
    {"source": "/a/:name", "destination": "/api/s?k=a&n=:name"},
    {"source": "/p/:name", "destination": "/api/s?k=p&n=:name"},
    {"source": "/g/:name", "destination": "/api/s?k=g&n=:name"},
    {"source": "/t/:name/:sub", "destination": "/api/s?k=t&n=:name&s=:sub"},
    {"source": "/t/:name", "destination": "/api/s?k=t&n=:name"},
    {"source": "/l/:name/:sub", "destination": "/api/s?k=l&n=:name&s=:sub"},
    {"source": "/l/:name", "destination": "/api/s?k=l&n=:name"},
    {"source": "/e/:name/:sub", "destination": "/api/s?k=e&n=:name&s=:sub"},
    {"source": "/e/:name", "destination": "/api/s?k=e&n=:name"},
    {"source": "/d/:name", "destination": "/api/s?k=d&n=:name"},
]

VERCEL = {
    "rewrites": REWRITES,
    "headers": [
        {
            # The pages are rebuilt only when a new export lands, and every
            # deploy ships fresh content in place, so a short cache with
            # revalidation keeps repeat visits instant without going stale.
            "source": "/(.*)",
            "headers": [{"key": "Cache-Control",
                         # A day at the edge meant a visitor could be served an
                         # HTML whose inlined build stamp named a database from
                         # before the artwork was added, and the covers never
                         # appeared. The page is cheap to revalidate; the
                         # database it points at is the expensive part and is
                         # versioned in its own url.
                         "value": "public, max-age=0, s-maxage=60, must-revalidate"}],
        },
        {
            # The database is the one big download and it only changes when a
            # new export is loaded, so let the browser keep it for a week
            # rather than re-fetching 5 MB on every visit.
            "source": "/listening.db",
            "headers": [{"key": "Cache-Control", "value": "public, max-age=604800"}],
        },
    ],
}


def write_summary(out, db):
    """One title and one sentence per subject, for the page a link lands on.

    Built here rather than in the function so that nothing has to be computed
    per request, and so the sentences come from the same database the page is
    drawn from. Only subjects with enough plays to say anything about are
    included; a link to anything rarer still works and simply carries the
    site's own description.
    """
    if not os.path.exists(db):
        json.dump({}, open(out, "w"))
        return 0
    c = sqlite3.connect(db)
    mine = "p.is_kid = 0 AND p.is_ambient = 0"
    last_year = c.execute("SELECT MAX(day) FROM plays").fetchone()[0][:4]
    m = {}

    # The cover the page shows, so a link arrives with its picture. Song and
    # album covers are stored at 300px; the same picture exists at 640.
    art = {(k, key): url for k, key, url in c.execute("SELECT kind, key, url FROM art")}
    SEP = "\x1f"

    def pic(kind, name, sub):
        u = (art.get((kind, f"{name}{SEP}{sub or ''}")) if kind in ("t", "l")
             else art.get(("p", sub)) if kind == "e" else art.get((kind, name)))
        return u.replace("ab67616d00001e02", "ab67616d0000b273") if u else None

    MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    day = lambda d: f"{MON[int(d[5:7]) - 1]} {int(d[8:10])}, {d[:4]}"

    def add(kind, name, sub, plays, days, first, last, extra=""):
        span = int(last[:4]) - int(first[:4]) + 1
        key = f"{kind}\x00{name.lower()}\x00{(sub or '').lower()}"
        what = {"a": "", "t": f" — {sub}", "l": f" — {sub}",
                "e": f" — {sub}", "p": "", "g": ""}[kind]
        # A show and a person are counted in episodes; one episode is counted
        # in plays, because 45 of the same episode is not 45 episodes.
        noun = "episodes" if kind in ("p", "g") else "plays"
        m[key] = {
            "t": f"{name}{what} · Listening",
            "d": (f"{plays:,} {noun} over {span} year{'s' if span != 1 else ''}, "
                  f"on {days:,} different days. "
                  f"First {day(first)}, last {day(last)}.{extra}"),
        }
        if pic(kind, name, sub):
            m[key]["i"] = pic(kind, name, sub)

    for name, plays, days, first, last in c.execute(f"""
            SELECT a.name, COUNT(*), COUNT(DISTINCT p.day), MIN(p.day), MAX(p.day)
              FROM plays p JOIN artists a ON a.id = p.artist_id
             WHERE {mine} GROUP BY a.id HAVING COUNT(*) >= 20"""):
        add("a", name, None, plays, days, first, last)
    # A person's figure counts episodes rather than plays, so the noun differs.

    for name, sub, plays, days, first, last in c.execute(f"""
            SELECT t.name, ifnull(ar.name, ''), COUNT(*), COUNT(DISTINCT p.day),
                   MIN(p.day), MAX(p.day)
              FROM plays p JOIN tracks t ON t.id = p.track_id
              LEFT JOIN artists ar ON ar.id = t.artist_id
             WHERE {mine} GROUP BY t.id HAVING COUNT(*) >= 20"""):
        add("t", name, sub, plays, days, first, last)

    for name, plays, days, first, last in c.execute(f"""
            SELECT s.name, COUNT(*), COUNT(DISTINCT p.day), MIN(p.day), MAX(p.day)
              FROM plays p JOIN shows s ON s.id = p.show_id
             WHERE {mine} GROUP BY s.id HAVING COUNT(*) >= 5"""):
        add("p", name, None, plays, days, first, last)

    # Albums are keyed on title and artist together, the same way the page and
    # the build key them, or a soundtrack would collide with a studio record.
    for name, sub, plays, days, first, last in c.execute(f"""
            SELECT t.album, ifnull(ar.name, ''), COUNT(*), COUNT(DISTINCT p.day),
                   MIN(p.day), MAX(p.day)
              FROM plays p JOIN tracks t ON t.id = p.track_id
              LEFT JOIN artists ar ON ar.id = t.artist_id
             WHERE {mine} AND t.album IS NOT NULL AND trim(t.album) <> ''
             GROUP BY t.album, t.artist_id HAVING COUNT(*) >= 20"""):
        add("l", name, sub, plays, days, first, last)

    # People are parsed out of episode titles rather than joined, so they have
    # no table to group by. The build has already done that work and left the
    # episode ids in the payload; without this a link to anyone in Top People
    # answered 404 while the page behind it worked perfectly well.
    # Episodes are summarised only where the page has a row for them, so the two
    # agree on which exist, but the figures are counted here: the row carries no
    # distinct-day count and reusing its play count for one would be a number
    # that happens to be present rather than one that is true.
    idx = os.path.join(WEB, "index.json")
    wanted = set()
    if os.path.exists(idx):
        payload = json.load(open(idx))
        cols = payload.get("cols") or []
        if cols:
            ci = {k: i for i, k in enumerate(cols)}
            wanted = {(r[ci["name"]], r[ci["sub"]])
                      for r in (payload.get("rows") or []) if r[ci["kind"]] == "e"}
    if wanted:
        for name, sub, plays, days, first, last in c.execute(f"""
                SELECT e.name, s.name, COUNT(*), COUNT(DISTINCT p.day),
                       MIN(p.day), MAX(p.day)
                  FROM plays p JOIN episodes e ON e.id = p.episode_id
                  JOIN shows s ON s.id = e.show_id
                 WHERE {mine} GROUP BY e.id"""):
            if (name, sub) in wanted:
                add("e", name, sub, plays, days, first, last)

    idx = os.path.join(WEB, "index.json")
    if os.path.exists(idx):
        eps = json.load(open(idx)).get("peopleEps") or {}
        for person, ids in eps.items():
            if not ids:
                continue
            marks = ",".join(str(int(i)) for i in ids)
            row = c.execute(f"""
                SELECT COUNT(*), COUNT(DISTINCT day), MIN(day), MAX(day)
                  FROM plays p
                 WHERE p.episode_id IN ({marks}) AND {mine}""").fetchone()
            if row and row[0]:
                add("g", person, None, *row)
    # A day: what it added up to and the song played most, the same pick the
    # day's own page leads with.
    DOW = "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split()
    import datetime
    tops = {}
    for d, name, artist in c.execute(f"""
            WITH x AS (SELECT p.day d, p.track_id id, COUNT(*) n, MIN(p.sec) s
                         FROM plays p WHERE {mine} AND p.track_id IS NOT NULL
                        GROUP BY p.day, p.track_id),
                 k AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY d ORDER BY n DESC, s) rk FROM x)
            SELECT k.d, t.name, ifnull(ar.name, '') FROM k JOIN tracks t ON t.id = k.id
              LEFT JOIN artists ar ON ar.id = t.artist_id WHERE k.rk = 1"""):
        tops[d] = (name, artist)
    for d, plays, ms in c.execute(f"""
            SELECT p.day, COUNT(*), SUM(p.ms) FROM plays p WHERE {mine} GROUP BY p.day"""):
        when = f"{DOW[datetime.date.fromisoformat(d).weekday()]}, {day(d)}"
        hrs = ms / 3.6e6
        desc = (f"{plays:,} play{'s' if plays != 1 else ''}, "
                + (f"{hrs:.1f} hours" if hrs >= 1 else f"{max(1, round(ms / 6e4))} min") + ".")
        if d in tops:
            desc += f" Most played: {tops[d][0]} by {tops[d][1]}."
        m[f"d\x00{d}\x00"] = {"t": f"{when} · Listening", "d": desc}
        if d in tops and pic("t", *tops[d]):
            m[f"d\x00{d}\x00"]["i"] = pic("t", *tops[d])
    c.close()
    json.dump(m, open(out, "w"), separators=(",", ":"), ensure_ascii=False)
    return len(m)


def main():
    os.makedirs(SITE, exist_ok=True)
    for src, dst in PAGES:
        path = os.path.join(WEB, src)
        if not os.path.exists(path):
            print(f"  skipping site/{dst} (no {src} yet)")
            continue
        html = open(path).read()
        out = os.path.join(SITE, dst)
        open(out, "w").write(html)
        print(f"  {src} -> site/{dst} ({os.path.getsize(out)/1e6:.1f} MB)")

    for asset in ("icon.svg", "apple-touch-icon.png"):
        src = os.path.join(WEB, asset)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(SITE, asset))
            print(f"  {asset} -> site/")

    # The Seasons pictures (season_art.py) and faces supplied by hand
    # (people_photos.txt points at web/people/).
    for d in ("seasons", "people", "share", "clips"):
        art = os.path.join(WEB, d)
        if os.path.isdir(art):
            shutil.copytree(art, os.path.join(SITE, d), dirs_exist_ok=True)
            print(f"  {d}/ -> site/ ({len(os.listdir(art))} pictures)")

    db = os.path.join(WEB, "listening.db")
    if os.path.exists(db):
        shutil.copy2(db, os.path.join(SITE, "listening.db"))
        print(f"  listening.db -> site/ ({os.path.getsize(db)/1e6:.1f} MB)")

    # Nothing here calls a paid service. The one function is the page a shared
    # link lands on: it answers with the right title and description and then
    # sends a person through to the explore page. The listening itself never
    # passes through it, only the name of the thing being linked to.
    for stale in ("node_modules", "package-lock.json", "package.json"):
        path = os.path.join(SITE, stale)
        if os.path.isdir(path):
            shutil.rmtree(path)
            print(f"  removed site/{stale}/")
        elif os.path.exists(path):
            os.remove(path)
            print(f"  removed site/{stale}")

    api_src = os.path.join(HERE, "api", "s.js")
    if os.path.exists(api_src):
        api_dst = os.path.join(SITE, "api")
        os.makedirs(api_dst, exist_ok=True)
        for f in os.listdir(os.path.join(HERE, "api")):
            if f.endswith(".js"):
                shutil.copy2(os.path.join(HERE, "api", f), os.path.join(api_dst, f))
        n = write_summary(os.path.join(api_dst, "summary.json"), db)
        print(f"  api/s.js + summary.json -> site/ ({n:,} subjects)")

    with open(os.path.join(SITE, "vercel.json"), "w") as f:
        json.dump(VERCEL, f, indent=2)
    print("  site/vercel.json")
    print("\nDeploy:  cd site && npx vercel --prod")


if __name__ == "__main__":
    main()
