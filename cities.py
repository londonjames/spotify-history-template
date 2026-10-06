"""Where the listening happened, from the ip_addr on every play.

Spotify stamps a connection country on each play and an IP address alongside
it. The IP is a lot more specific, and a lot less trustworthy: what it names
is where the connection left the network, which for a mobile carrier is a hub
that can be several hundred miles from the phone. Three rules get it honest.

1. The country has to agree. Spotify's own conn_country is the check, and it
   is the one that removes a VPN.

2. A day belongs to one place. Where two cities appear on the same day, the
   one with fewer plays is the other connection on the same trip -- the phone
   against the wifi -- so the day goes to whichever had more. That is what
   catches a suburb sitting underneath the city it belongs to.

3. Some places the data simply names wrong, because the exit is nowhere near
   the person. Those are listed by hand in config.json ("not_a_trip"), and a
   town in the home country on one stray day is dropped as well.

Nothing here is published but city names, counts and years. The addresses stay
on this machine.
"""
import bisect, collections, csv, datetime, glob, gzip, ipaddress, json, os, sys
import urllib.request

import config

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORTS = os.path.join(HERE, "exports")
OUT = os.path.join(HERE, "web", "cities.json")
CACHE = os.path.join(HERE, ".dbip")
FEED = "https://download.db-ip.com/free/dbip-city-lite-{}.csv.gz"

# The metro anyone would name, for an exit that is really part of it. Add your
# own in config.json under "rename" ({"Newark": "New York"}); these are a start.
RENAME = {
    "Newark": "New York", "Northlake": "Chicago", "Tempe": "Phoenix",
    "Chalk Farm": "London", "Greenford": "London", "Luton": "London",
    "Campbellfield": "Sydney", "Chiyoda City": "Tokyo",
    "Yongsan-dong": "Seoul", "Seongnam-si": "Seoul", "Lijnden": "Amsterdam",
    "Paris 18 Buttes-Montmartre": "Paris", "Central": "Hong Kong",
    "Etobicoke": "Toronto",
}
RENAME.update(config._c.get("rename") or {})
# Serves the whole country from one town, so it places nothing.
DISCARD = {"Monroe"}

# Home is a country and a set of towns in it (config.json). Until the towns are
# listed nothing counts as home, and this script prints where the listening
# was so they can be picked out.
HC, HOME = config.HOME_COUNTRY, config.HOME_CITIES

US_STATE = {
    "New York": "NY", "Massachusetts": "MA", "California": "CA", "Illinois": "IL",
    "Arizona": "AZ", "Nevada": "NV", "Texas": "TX", "Florida": "FL", "Oregon": "OR",
    "Washington": "WA", "Colorado": "CO", "Utah": "UT", "Hawaii": "HI",
    "District of Columbia": "DC", "New Jersey": "NJ", "Pennsylvania": "PA",
    "Louisiana": "LA", "Georgia": "GA", "Michigan": "MI", "Minnesota": "MN",
    "North Carolina": "NC", "Virginia": "VA", "Tennessee": "TN", "Ohio": "OH",
    "Wisconsin": "WI", "Missouri": "MO", "Maine": "ME", "Vermont": "VT",
    "New Hampshire": "NH", "Rhode Island": "RI", "Connecticut": "CT",
    "Maryland": "MD", "South Carolina": "SC", "Alabama": "AL", "Indiana": "IN",
    "Idaho": "ID", "Montana": "MT", "Wyoming": "WY", "New Mexico": "NM",
    "Oklahoma": "OK", "Arkansas": "AR", "Iowa": "IA", "Kansas": "KS",
    "Nebraska": "NE", "Kentucky": "KY", "Mississippi": "MS", "Delaware": "DE",
    "West Virginia": "WV", "Alaska": "AK", "South Dakota": "SD", "North Dakota": "ND",
}


def dataset():
    """The free DB-IP city table, fetched once a month and kept locally."""
    os.makedirs(CACHE, exist_ok=True)
    for back in range(0, 4):
        today = datetime.date.today()
        y, m = today.year, today.month - back
        while m < 1: y, m = y - 1, m + 12
        tag = f"{y}-{m:02d}"
        path = os.path.join(CACHE, f"dbip-{tag}.csv.gz")
        if os.path.exists(path):
            return path
        try:
            # requests, not urllib: some Python installs ship without the
            # certificates urllib needs, and the download fails silently.
            import requests
            r = requests.get(FEED.format(tag), timeout=120)
            r.raise_for_status()
            open(path, "wb").write(r.content)
            print(f"  fetched {tag}")
            return path
        except Exception:
            if os.path.exists(path): os.remove(path)
    sys.exit("could not fetch the DB-IP city table")


def table(path):
    starts = {4: [], 6: []}, {4: [], 6: []}, {4: [], 6: []}
    s, e, d = starts
    with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 6: continue
            try:
                a = ipaddress.ip_address(row[0]); b = ipaddress.ip_address(row[1])
            except ValueError:
                continue
            v = a.version
            s[v].append(int(a)); e[v].append(int(b)); d[v].append((row[3], row[4], row[5], row[6], row[7]) if len(row) > 7 else (row[3], row[4], row[5], "", ""))
    return s, e, d


def main():
    s, e, d = table(dataset())
    print(f"  ranges: {len(s[4]):,} v4, {len(s[6]):,} v6")

    seen = {}
    def where(ip):
        if ip not in seen:
            try:
                x = ipaddress.ip_address(ip)
                i = bisect.bisect_right(s[x.version], int(x)) - 1
                seen[ip] = (d[x.version][i] if i >= 0 and int(x) <= e[x.version][i] else None)
            except ValueError:
                seen[ip] = None
        return seen[ip]

    # Rule 1 as the rows are read; rule 2 needs the whole day first.
    byday = collections.defaultdict(collections.Counter)
    msday = collections.defaultdict(collections.Counter)
    region = collections.defaultdict(collections.Counter)
    # The same count keyed on the day as the site keeps it (Pacific time), with
    # a position for each place, for the map on a day's own page.
    from zoneinfo import ZoneInfo
    import datetime
    LA = ZoneInfo(config.TZ)
    local = collections.defaultdict(collections.Counter)
    spot = collections.defaultdict(lambda: [0.0, 0.0, 0])
    for f in sorted(glob.glob(os.path.join(EXPORTS, "**", "*.json"), recursive=True)):
        try: rows = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        for r in rows:
            ip, cc = r.get("ip_addr"), r.get("conn_country")
            if not ip: continue
            g = where(ip)
            if not g: continue
            if cc and cc != "ZZ" and g[0] != cc: continue
            city = RENAME.get(g[2].split(" (")[0], g[2].split(" (")[0])
            if city in DISCARD: continue
            # Keyed on the city alone. Newark carries New Jersey with it and
            # Campbellfield carries Victoria, so keeping the region in the key
            # left a second New York and a second Sydney standing beside the
            # first. The region is only ever a label, chosen below.
            byday[r["ts"][:10]][(g[0], city)] += 1
            msday[r["ts"][:10]][(g[0], city)] += r.get("ms_played") or 0
            region[(g[0], city)][g[1]] += 1
            try:
                ld = datetime.datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) \
                    .astimezone(LA).date().isoformat()
                local[ld][(g[0], city)] += 1
                sp = spot[(g[0], city)]
                sp[0] += float(g[3]); sp[1] += float(g[4]); sp[2] += 1
            except (ValueError, KeyError):
                pass

    place = collections.defaultdict(lambda: [0, set(), 0])
    for day, counts in byday.items():
        # Rule 2: the day goes wholesale to whichever place had the most of it.
        top = counts.most_common(1)[0][0]
        rec = place[top]
        rec[0] += sum(counts.values())
        rec[1].add(day[:4])
        rec[2] += sum(msday[day].values())

    # Until home towns are listed, the whole home country counts as home, so
    # the page does not call every day a day away.
    is_bay = lambda cc, city: cc == HC and (not HOME or city in HOME)
    bay = sum(n for (cc, city), (n, _, _) in place.items() if is_bay(cc, city))
    total = sum(n for n, _, _ in place.values())
    # Hours away from home, in the country and abroad together: what each row's
    # share on the page is a share of.
    away_ms = sum(ms for (cc, city), (_, _, ms) in place.items() if not is_bay(cc, city))
    # A trip abroad is a share of the time abroad, not of the time away.
    abroad_ms = sum(ms for (cc, _), (_, _, ms) in place.items() if cc != HC)

    def entry(label, n, years, ms, of):
        y = sorted(int(v) for v in years)
        span = str(y[0]) if len(y) == 1 else f"{y[0]}–{y[-1]}"
        return [label, n, span, len(y), round(ms / 3.6e6), round(100 * ms / of, 1)]

    us, abroad = [], []
    for (cc, city), (n, years, ms) in place.items():
        if cc == HC:
            if not HOME or city in HOME: continue
            # Whichever state most of its plays came from: New York's own rows
            # outnumber the ones that arrived through Newark.
            reg = region[(cc, city)].most_common(1)[0][0]
            st = US_STATE.get(reg, reg)
            # "Washington D.C., DC" says the district twice.
            label = "Washington DC" if st == "DC" else f"{city}, {st}"
            us.append(entry(label, n, years, ms, away_ms))
        else:
            abroad.append(entry(f"{city}, {cc}", n, years, ms, abroad_ms))
    # By hours, which is what the page shows; by plays put Chicago's 62 hours
    # below Phoenix's 49.
    us.sort(key=lambda r: -r[4]); abroad.sort(key=lambda r: -r[4])

    out = {"total": total, "bay": bay,
           "bayPct": round(100 * bay / total),
           "awayHours": round(away_ms / 3.6e6),
           "abroadHours": round(abroad_ms / 3.6e6),
           "abroadPlaces": len(abroad),
           "abroadCountries": len({r[0].rsplit(", ", 1)[1] for r in abroad}),
           "us": us[:10], "abroad": abroad[:10]}
    json.dump(out, open(OUT, "w"), separators=(",", ":"), ensure_ascii=False)

    # Days away, for the map: the place that had most of the day, where that
    # place is not home. It needs three plays on the day. Only the name and a
    # position rounded to a tenth of a degree (about seven miles) are written.
    # NOT_A_TRIP names towns where a network hands its traffic over, such as
    # the data centres in Ashburn and Sterling, Virginia.
    NOT_A_TRIP = config.NOT_A_TRIP
    picked = {}
    for day, counts in sorted(local.items()):
        (cc, city), n = counts.most_common(1)[0]
        if is_bay(cc, city) or n < 3 or city in NOT_A_TRIP:
            continue
        picked[day] = (cc, city)
    near = lambda day, key: any(
        picked.get((datetime.date.fromisoformat(day) + datetime.timedelta(d)).isoformat()) == key
        for d in (-3, -2, -1, 1, 2, 3))
    names, days, index = [], {}, {}
    for day, key in picked.items():
        cc, city = key
        # A town in the home country seen on one day, with no other day there
        # that week, is a carrier's exit or a flight overhead, not a trip.
        # Abroad, the country has already been checked against Spotify's own.
        if cc == HC and not near(day, key):
            continue
        if key not in index:
            reg = region[key].most_common(1)[0][0]
            st = US_STATE.get(reg, reg)
            label = (("Washington DC" if st == "DC" else f"{city}, {st}")
                     if cc == "US" else f"{city}, {cc}")
            la, lo, k = spot[key]
            index[key] = len(names)
            names.append([label, round(la / k, 1), round(lo / k, 1)])
        days[day] = index[key]
    json.dump({"places": names, "days": days},
              open(os.path.join(HERE, "web", "away.json"), "w"),
              separators=(",", ":"), ensure_ascii=False)
    print(f"  {len(days):,} days away in {len(names)} places -> web/away.json")
    print(f"  {total:,} located, {out['bayPct']}% at home ({config.HOME_LABEL})")
    if not HOME:
        print("  config.json lists no home towns yet. The places with the most plays:")
        for (cc, city), (n, _, _) in sorted(place.items(), key=lambda kv: -kv[1][0])[:15]:
            print(f"    {n:>7,}  {city}, {cc}")
        print('  Add the ones that are home to "home": {"cities": [...]} and run this again.')
    for r in out["us"]: print(f"    US      {r[1]:>6,}  {r[0]:<22} {r[2]}  {r[3]}y")
    for r in out["abroad"]: print(f"    abroad  {r[1]:>6,}  {r[0]:<22} {r[2]}  {r[3]}y")
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
