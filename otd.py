"""The song the page's "On this day" card shows for every date in the history.

The card works it out in the browser; this is the same query, run over every
day of the year at once, so art.py can fetch a cover for each and check_art.py
can refuse a build in which one would show an empty square.
"""
import os


def picks(db, prefer_pic=True):
    """(month-day, year, track, artist, album, uri, plays that day, has a
    picture) per day: of the songs played that day, the one played most that year.
    A song with a picture is preferred to one without, so a
    recording Spotify has since withdrawn does not leave an empty square."""
    # Bedtime songs were played nearly every night for years, so they would be
    # the answer on every date of those years. They are the last resort: shown
    # only on a day when nothing else was played.
    here = os.path.dirname(os.path.abspath(__file__))
    bed = []
    path = os.path.join(here, "bedtime.txt")
    for line in open(path) if os.path.exists(path) else []:
        if "::" in line and not line.startswith("#"):
            artist, track = [x.strip() for x in line.split("::")][:2]
            bed.append(track + chr(31) + artist)
    marks = ",".join("?" * len(bed)) or "''"
    order = ("pic DESC, " if prefer_pic else "") + "bed, c DESC, n DESC, s"
    return db.execute(f"""
        WITH d AS (
          SELECT p.day day, p.track_id id, COUNT(*) n, MIN(p.sec) s
            FROM plays p
           WHERE p.is_kid = 0 AND p.is_ambient = 0 AND p.track_id IS NOT NULL
           GROUP BY p.day, p.track_id),
        yc AS (
          SELECT substr(p.day, 1, 4) y, p.track_id id, COUNT(*) c
            FROM plays p
           WHERE p.is_kid = 0 AND p.is_ambient = 0 AND p.track_id IS NOT NULL
           GROUP BY y, p.track_id),
        j AS (SELECT d.*, yc.c c, t.name tn, ifnull(a.name, '') an, t.album al, t.uri uri,
                     x.url IS NOT NULL pic,
                     t.name || char(31) || ifnull(a.name, '') IN ({marks}) bed
                FROM d JOIN yc ON yc.id = d.id AND yc.y = substr(d.day, 1, 4)
                JOIN tracks t ON t.id = d.id
                LEFT JOIN artists a ON a.id = t.artist_id
                LEFT JOIN art x ON x.kind = 't'
                               AND x.key = t.name || char(31) || ifnull(a.name, '')),
        k AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY day ORDER BY {order}) rk
                FROM j)
        SELECT substr(day, 6, 5), substr(day, 1, 4), tn, an, al, uri, n, pic
          FROM k WHERE rk = 1""", bed).fetchall()
