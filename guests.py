#!/usr/bin/env python3
"""Pull person names out of podcast episode titles.

Spotify's export has no guest field, so the episode title is the only record of
who was on. Every show writes them differently, so this applies patterns in
order of confidence:

    "Gwyneth Paltrow"                       SmartLess: the title IS the guest
    "Chris Pratt: Yes/No"                   Rob Lowe: guest before the colon
    "'Skyfall' With Chris Ryan, and ..."    Rewatchables: panel after "With"
    "... With Kara Swisher | Bill Simmons"  trailing show name stripped first

Treating a bare title as a guest is only safe on shows that name episodes after
the guest. "Hot Girl" is an Office Ladies episode, not a person, so that rule is
switched on per show by bare_name_shows() rather than applied everywhere.
"""
import re
from collections import defaultdict

QUOTES = "‘’“”\"'"
_Q = re.escape(QUOTES)

# Words that make a capitalised phrase something other than a person.
NOT_NAME = {
    "the", "a", "an", "and", "or", "of", "with", "part", "episode", "live",
    "special", "vs", "versus", "best", "worst", "top", "new", "old", "how",
    "why", "what", "when", "movie", "movies", "film", "films", "show",
    "podcast", "season", "finale", "draft", "nba", "nfl", "mlb", "trade",
    "mailbag", "preview", "recap", "review", "awards", "week", "day", "night",
    "year", "hall", "fame", "guys", "hour", "minutes", "volume", "christmas",
    "halloween", "holiday", "anniversary", "reunion", "finals", "rewatch",
    "girl", "boy", "man", "woman", "care", "health", "office", "club", "hot",
    "quick",  # "Quick Served", a segment of Served, not a guest
}
PARTICLES = {"de", "van", "von", "der", "del", "da", "di", "la", "le", "mc",
             "o", "st", "bin", "al"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
# Narration that trails a name in a headline rather than belonging to it.
TRAILING = {"returns", "joins", "talks", "explains", "answers", "interview",
            "live", "again", "part", "pt", "plus", "update", "tribute",
            "special", "returns!", "episode"}


def looks_like_name(s):
    """True when s reads as a person's name rather than a title or heading."""
    s = s.strip(QUOTES + " .-—–")
    if not (3 <= len(s) <= 40) or any(ch.isdigit() for ch in s):
        return False
    toks = s.split()
    if not (2 <= len(toks) <= 4):
        return False               # single words are too ambiguous to trust
    for t in toks:
        bare = re.sub(r"[^A-Za-z'’.-]", "", t).lower().rstrip(".")
        if not bare:
            return False
        if bare in PARTICLES or bare in SUFFIXES:
            continue
        if bare in NOT_NAME:
            return False
        if not t[:1].isupper():
            return False           # every real token is capitalised
    return True


def split_people(chunk):
    """Split 'A, B, and C' / 'A & B' into individual names."""
    chunk = re.sub(r"\s+&\s+", ", ", chunk)
    chunk = re.sub(r"\band\b", ",", chunk, flags=re.I)
    return [p.strip() for p in chunk.split(",") if p.strip()]


def _clean(p):
    """Trim brackets, leading articles and trailing narration off one name."""
    p = re.sub(r"\s*\(.*?\)\s*", " ", p).strip(QUOTES + " .-—–")
    p = re.sub(r"^(the|a)\s+", "", p, flags=re.I).strip()
    toks = p.split()
    while len(toks) > 2 and toks[-1].lower().strip("!?.") in TRAILING:
        toks.pop()
    return " ".join(toks)


def _strip_quoted_work(t):
    """Remove a quoted work ('Skyfall') unless the quotes span the whole title.

    A quote covering the entire line is a SmartLess-style guest name, so it is
    kept; anything shorter is the film or book under discussion.
    """
    m = re.search(rf"[{_Q}][^{_Q}]{{2,}}[{_Q}]", t)
    if m and m.group(0).strip() != t.strip():
        t = (t[:m.start()] + " " + t[m.end():])
    return t


def extract(episode, allow_bare=False):
    """Return the people named in one episode title.

    allow_bare turns on the rule that an entire title is a guest's name, which
    is only true for shows whose episodes are named after the guest.
    """
    if not episode:
        return []
    t = re.sub(r"\s*\|\s*[^|]+$", "", episode.strip())   # drop "| Show Name"
    t = _strip_quoted_work(t).strip(QUOTES + " ")
    # "#419 – Sam Altman: ..." and "#582: Mark Zuckerberg on ...": the episode
    # number is not part of anything.
    t = re.sub(r"^#?\d+\s*[:–—-]\s*", "", t)

    # 1. Panel/guest list after "With". Whatever precedes it is the topic or
    #    film, so it is dropped rather than scanned for names.
    m = re.search(r"\bwith\b(.+)$", t, flags=re.I)
    if m:
        out = split_people(m.group(1))
    elif ":" in t:
        # 2. "Guest Name: tagline" -- only the part before the first colon.
        #    "Mark Zuckerberg on Long-Term Strategy: ..." keeps the name only.
        head = re.split(r"\s+on\s+", t.split(":", 1)[0], maxsplit=1)[0]
        out = split_people(head)
    elif re.search(r"\s[–—-](\s|$)", t):
        # 2b. "Elon Musk - In 36 months ...": a name, a dash, the topic.
        out = split_people(re.split(r"\s[–—-](?:\s|$)", t, maxsplit=1)[0])
    elif re.search(r"\s+on\s+", t):
        # 2c. "Mark Zuckerberg on Long-Term Strategy": the name before "on".
        #     A title that does not start with a name fails looks_like_name.
        out = [re.split(r"\s+on\s+", t, maxsplit=1)[0]]
    elif allow_bare:
        # 3. The title is the guest outright.
        out = split_people(t)
    else:
        out = []

    seen, names = set(), []
    for p in out:
        p = _clean(p)
        if looks_like_name(p) and p.lower() not in NOT_PEOPLE:
            key = p.lower().replace(".", "")
            if key not in seen:
                seen.add(key)
                names.append(p)
    return names


# Two-word phrases that read as names and are not people.
NOT_PEOPLE = {"roland garros", "nis conversations"}

# Short forms that titles use for people who are also named in full elsewhere.
ALIASES = {"zuck": "Mark Zuckerberg", "elon": "Elon Musk"}


def mentions(episode, known):
    """Everyone in `known` named anywhere in a title, plus ALIASES.

    extract() only trusts the positions a guest is written in. A title that is
    about someone without them on it -- "Walter Isaacson: Elon Musk, Steve
    Jobs, ..." -- names them all the same, and listening to it is listening to
    them. `known` maps a lowercased name to its display form and is everyone
    extract() found across all titles, so only names already established as
    people are matched.
    """
    if not episode:
        return []
    t = re.sub(r"\s*\|\s*[^|]+$", "", episode.strip())
    low = t.lower()
    out = []
    for key, name in known.items():
        if key in low and re.search(rf"(?<![\w]){re.escape(key)}(?![\w])", low):
            out.append(name)
    for a, name in ALIASES.items():
        if name not in out and re.search(rf"(?<![\w]){a}(?![\w])", low):
            out.append(name)
    return out


def bare_name_shows(rows, threshold=0.75, minimum=5):
    """Shows whose episode titles are mostly bare guest names.

    rows is (show, episode) pairs. A show qualifies when over `threshold` of its
    distinct titles - ones with no "with" or colon to key off - still parse as a
    person's name, which is what SmartLess looks like and Office Ladies does not.
    """
    seen = defaultdict(set)
    for show, ep in rows:
        if show and ep:
            seen[show].add(ep)
    out = set()
    for show, eps in seen.items():
        plain = [e for e in eps
                 if not re.search(r"\bwith\b", e, flags=re.I) and ":" not in e]
        if len(plain) < minimum:
            continue
        hits = sum(1 for e in plain
                   if looks_like_name(_clean(e.strip(QUOTES + " "))))
        if hits / len(plain) > threshold:
            out.add(show)
    return out
