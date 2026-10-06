#!/usr/bin/env python3
"""Inline each page's JSON into its template to produce standalone HTML.

The payload sits in a <script type="application/json"> tag rather than being
fetched, so a page opens straight off disk with no server and no CORS. What is
inlined is only the part needed before anything is clicked -- listening.db is
fetched over the network afterwards, because 20 MB inlined would mean waiting
for all of it before seeing any of it.
"""
import json
import os

import config

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")

PAGES = [
    ("app.html", "index.json", "listening-history.html"),
    ("story.html", "story.json", "listening-story.html"),
]


def extras(data):
    """The payload, with two small things the main page needs that its own
    builder does not make: the seasons (so an artist's page can say which ones
    they were in) and the bedtime songs (bedtime.txt)."""
    raw = open(data).read()
    if os.path.basename(data) != "index.json":
        return raw
    d = json.loads(raw)
    story = os.path.join(WEB, "story.json")
    if os.path.exists(story):
        ph = json.load(open(story))["phases"]
        d["seasons"] = [[s["title"], s["span"]["y0"], s["span"]["y1"]]
                        for s in ph if not s.get("bonus")]
        # One of the season pictures, for the banner that leads to them.
        d["seasonArt"] = next((s["art"] for s in reversed(ph)
                               if s.get("art") and not s.get("bonus")), None)
    # Where "away" is measured from, for the map and the wording around it.
    d["bedLabel"] = config._c.get("bedtime_label") or "BEDTIME"
    d["home"] = {"label": config.HOME_LABEL, "lat": config.HOME_LAT, "lon": config.HOME_LON}
    bed = os.path.join(HERE, "bedtime.txt")
    if os.path.exists(bed):
        d["bedtime"] = [[x.strip() for x in line.split("::", 2)]
                        for line in open(bed)
                        if "::" in line and not line.lstrip().startswith("#")]
    return json.dumps(d, separators=(",", ":"), ensure_ascii=False)


def inline(tpl, data, out):
    """Fill a template's __DATA__ slot, or pass it through if it has none.

    A page that carries its own content is a legitimate state -- a template
    being drafted by hand before its data exists is not a broken build, and
    refusing it would be the build enforcing a stage of the work rather than
    the shape of the output.
    """
    html = open(tpl).read()
    if "__DATA__" in html:
        # The payload sits inside a <script> tag, so any "<" in a track title
        # could end it early. Escaping "<" keeps it valid JSON and inert markup.
        html = html.replace("__DATA__", extras(data).replace("<", "\\u003c"))
    open(out, "w").write(html)
    return os.path.getsize(out)


def check_scripts(path):
    """Stop the build if a page's script will not parse.

    A stray "else" once shipped and left the live page blank: the HTML is
    valid whatever the script says, so nothing else here would notice. Needs
    node; without it the check is skipped and says so.
    """
    import re
    import shutil
    import subprocess
    import sys
    import tempfile
    if not shutil.which("node"):
        print(f"  {os.path.basename(path)}: script not checked (no node)")
        return
    html = open(path).read()
    for i, js in enumerate(re.findall(
            r"<script(?![^>]*application/json)(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)):
        if not js.strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js)
        r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
        os.unlink(f.name)
        if r.returncode:
            sys.exit(f"{os.path.basename(path)}: script {i + 1} does not parse\n"
                     + r.stderr.strip()[:600])


built = 0
for tpl, data, out in PAGES:
    tp, dp, op = (os.path.join(WEB, f) for f in (tpl, data, out))
    if not os.path.exists(tp):
        print(f"  skipping {tpl} (no template yet)")
        continue
    if "__DATA__" in open(tp).read() and not os.path.exists(dp):
        print(f"  skipping {tpl} (waiting on {data})")
        continue
    check_scripts(tp)
    print(f"  {out} ({inline(tp, dp, op)/1e6:.1f} MB)")
    built += 1

db = os.path.join(WEB, "listening.db")
if os.path.exists(db):
    print(f"  listening.db ({os.path.getsize(db)/1e6:.1f} MB, fetched not inlined)")
print("  ./serve to look at it")
