"""The handful of facts about the person whose listening this is.

Everything personal lives in config.json and the .txt lists beside it, so the
scripts and the two page templates are the same for anyone.

  name        how the account's owner is labelled inside spotify.db
  timezone    the home zone; "when do I listen" is asked in local time
  site        the public address if the site is published ("" when it is not)
  home        where "away" is measured from: a label for the map, a point for
              the line, the country code, and the towns that count as home.
              `cities` may be left empty at first -- run cities.py, read the
              list of places it prints, and add the ones that are home.
  not_a_trip  towns that are a network's exit rather than somewhere visited
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_c = json.load(open(os.path.join(HERE, "config.json")))

NAME = _c.get("name") or "Me"
TZ = _c.get("timezone") or "UTC"
SITE = (_c.get("site") or "").rstrip("/")
HOME = _c.get("home") or {}
HOME_LABEL = HOME.get("label") or "home"
HOME_LAT, HOME_LON = HOME.get("lat", 0.0), HOME.get("lon", 0.0)
HOME_COUNTRY = HOME.get("country") or "US"
HOME_CITIES = set(HOME.get("cities") or [])
NOT_A_TRIP = set(_c.get("not_a_trip") or [])
