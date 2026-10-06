#!/usr/bin/env python3
"""Turn Spotify's raw platform strings into something you can group by.

The export records the device, not the kind of device: 361 distinct strings for
what are really nine things. "Partner amazon_echo Amazon;Echo;;" and
"Partner amazon_salmon Amazon;Echo_Dot;27d4...;;tpapi" are both a kitchen
speaker; "iOS 14.4.2 (iPhone10,3)" and a bare "ios" are both a phone.

The grouping is by listening context, because that is the question worth asking
of it -- a speaker in the house is a different kind of listening from headphones
on a walk, and the Echo share is the strongest signal in the data for the
kids-versus-owner split.

Two caveats worth keeping honest:
  * A bare "ios" (34,949 plays) does not say which device. Modern exports stopped
    including the model, so it stays "iOS" rather than being guessed into iPhone.
  * "not_applicable" (20,287 plays) is Spotify's own blank. It is Unknown, not a
    device, and should not be drawn as one.
"""

# Ordered: the first pattern that matches wins, so the specific cases (an iPad
# model string) are tested before the general one (anything starting with iOS).
RULES = [
    ("Echo",    lambda p: "amazon" in p or "echo" in p),
    ("Sonos",   lambda p: "sonos" in p),
    # Google Home answers to "cast" and reports itself three different ways.
    ("Cast",    lambda p: p == "cast" or "cast_voice" in p or "google_home" in p),
    ("iPad",    lambda p: p.startswith("ios") and "(ipad" in p),
    ("iPhone",  lambda p: p.startswith("ios") and "(iphone" in p),
    ("iOS",     lambda p: p == "ios" or p.startswith("ios ")),
    ("Android", lambda p: p.startswith("android")),
    ("Mac",     lambda p: p == "osx" or p.startswith("os x") or "macos" in p),
    ("Web",     lambda p: p.startswith("web_player") or p.startswith("webplayer")
                          or "web_player" in p or p == "chrome"),
    ("Windows", lambda p: p == "windows" or p.startswith("windows ")),
    ("Unknown", lambda p: p in ("", "not_applicable", "unknown")),
]

# Speakers are shared and in a room; the rest are one person with a screen.
IN_THE_HOUSE = {"Echo", "Sonos"}


def device_of(platform):
    """Map a raw platform string to one of nine labels."""
    p = (platform or "").strip().lower()
    for label, test in RULES:
        if test(p):
            return label
    return "Other"


if __name__ == "__main__":
    import collections
    import os
    import sqlite3
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spotify.db")
    c = sqlite3.connect(db)
    tally = collections.Counter()
    unmapped = collections.Counter()
    for n, p in c.execute("SELECT COUNT(*), platform FROM plays GROUP BY platform"):
        d = device_of(p)
        tally[d] += n
        if d == "Other":
            unmapped[p] += n
    total = sum(tally.values())
    for d, n in tally.most_common():
        print(f"  {d:<9} {n:>7,}  {100*n/total:5.1f}%")
    if unmapped:
        print("\nfell through to Other:")
        for p, n in unmapped.most_common(15):
            print(f"  {n:>6,}  {p[:70]}")
