# spotify-history

Your whole Spotify history as a site you can explore: most played artists,
songs, albums, podcasts and podcast guests; a page for every one of them; a
calendar of every day with a page per day; a map for days away from home; and
cards such as "On this day", "Discoveries by year" and "When the music was
made". Optionally, a "Seasons" story you write yourself.

Everything runs on your own machine. Your export, the database built from it
and the finished site stay there unless you choose to publish the site.

## What you need

- Python 3.10+ and Node (Node only checks the page scripts; the build runs without it)
- `pip install -r requirements.txt`
- Your **extended streaming history** from spotify.com/account/privacy.
  Spotify takes up to 30 days to send it.
- A free Spotify developer app for covers, artist photos and release years:
  create one at developer.spotify.com/dashboard, then put its two values in a
  file called `.env.spotify` in this folder:

      SPOTIFY_CLIENT_ID=...
      SPOTIFY_CLIENT_SECRET=...

  The file is git-ignored. Without it the site still builds, with plain
  squares where pictures would be.

## First run

1. Edit `config.json`:
   - `name`: any label for you.
   - `timezone`: your home zone, e.g. `Europe/London`.
   - `home`: a `label` as the site should word it ("London", "the Bay Area"),
     the `lat` and `lon` of roughly where you live, and your `country` code.
     Leave `cities` empty for now.
2. Leave Spotify's `.zip` in `~/Downloads` (don't unzip it) or put it in `exports/`.
3. Run `./refresh`.
   - It stops once to ask about podcasts: every show needs a verdict before it
     can appear. `python3 review.py --keep-all` clears them all, or use
     `--hide "Show Name"` for any you would not want on a page. Then run
     `./refresh` again.
   - The first run looks up a cover for every song and album, so allow 20 to
     30 minutes. Later runs only fetch what is new. Photos of podcast guests
     come from Wikipedia 250 at a time, so each further `./refresh` adds more.
4. Run `./serve` and open the address it prints.

## Making it yours

- **Home towns.** The first run prints the places with the most plays. Add the
  ones that are home to `home.cities` in `config.json` and run `./refresh`.
  Until you do, your whole country counts as home and only trips abroad show
  as days away. Towns that are a network's exit and nowhere you went go in
  `not_a_trip`; a suburb that should read as its city goes in `rename`.
- **Other people on your account.** `kids.txt` tags artists, tracks or albums
  that are someone else's listening. They stay in the totals under "Everyone"
  and leave your own lists.
- **Songs you played for someone.** `bedtime.txt` tags them and can show a
  sentence on the song's page. The tag's wording is `bedtime_label` in `config.json`.
- **Private podcasts.** `private.txt` keeps a show off every page.
- **A speaker left on.** `ambient_sql` in `config.json` is a condition over the
  plays table for playback nobody was listening to (see `publish_db.py`).
- **Faces for podcast guests.** `people_photos.txt` overrides the automatic lookup.

## Seasons (optional)

The Seasons page tells your history as a handful of periods. The windows and
the words are yours; every figure, cover, top song and top artist is measured.

1. Copy `seasons.example.json` to `seasons.json` and set your own windows
   (`id`, `from`, `to`, optionally a `signature` track to play).
2. Copy `story_copy.example.json` to `story_copy.json` and write a `title`
   (24 characters at most) and exactly four `bullets` (34 characters at most,
   no full stop) for each id. Any number you quote is checked against the data.
3. `./refresh`. The banner to Seasons appears on the home page.

Pictures behind each season are optional too. `season_art.py` generates them
with a paid OpenAI key from prompts you write in `season_art.json`; any image
saved as `web/seasons/<id>.jpg` works as well.

## When a new export arrives

Leave the new `.zip` in `~/Downloads` and run `./refresh`. Exports overlap
almost completely and the loader de-duplicates, so it is always safe to rerun.

## Publishing (optional)

`site/` is a complete static site. `cd site && npx vercel --prod` puts it on
Vercel, where the song clips and share previews work through two small
functions in `api/`. Set `site` in `config.json` to its address, and run with
`STRICT_ART=1 ./refresh` so a missing cover stops the build.

## What is where

    refresh            the whole pipeline, in order
    serve              look at site/ on this machine
    config.json        the facts about you
    *.txt              your tag lists
    load.py            exports -> spotify.db (everything, private, stays here)
    publish_db.py      spotify.db -> web/listening.db (what the pages read)
    web_data.py        the home page's data
    cities.py          where the listening happened, from the export's IP addresses
    web/app.html       the site; web/story.html is Seasons
    sh.py              ask the database questions from the terminal
