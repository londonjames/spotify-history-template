#!/usr/bin/env python3
"""Podcast shows and audiobooks held back from every published page.

Read from private.txt, one title per line. Music is never filtered -- every song
and artist is published; spoken word is the only surface with a judgment call
attached.

Audiobooks are checked here as well as podcasts. They used to be safe only by
accident: no collector in export.py ever emitted an audiobook row, so adult
titles sat unguarded in the database and would have surfaced the moment anything
queried it directly. Callers pass COALESCE(show, audiobook) so one list covers
both.

The filter is applied where the plays are read, not where they are rendered, so
an excluded title disappears from the search index, the leaderboards, the people
list, the published database and the seasonal story at once. Hours still count
toward the totals -- the listening happened, it just isn't attributed on screen.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "private.txt")


def load():
    """Return the set of blocked titles, lowercased."""
    names = set()
    if not os.path.exists(FILE):
        return names
    for line in open(FILE):
        line = line.split("#")[0].strip()
        if line:
            names.add(line.lower())
    return names


def make_filter():
    """Return blocked(title) -> bool for the current private.txt.

    Accepts a podcast show name or an audiobook title; the list is one namespace.
    """
    names = load()

    def blocked(title):
        return bool(title) and title.strip().lower() in names

    return blocked
