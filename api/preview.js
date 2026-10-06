// The 30-second clip Spotify publishes for a track, for the play buttons on
// song rows. A browser cannot read Spotify's embed page itself, so this reads
// it and redirects to the clip's own address; the audio then comes straight
// from Spotify. No key, no paid service, and nothing about the listener passes
// through. Answers are cached for a week.
module.exports = async (req, res) => {
  const id = String((req.query && req.query.id) || "");
  if (!/^[A-Za-z0-9]{22}$/.test(id)) { res.statusCode = 400; return res.end("bad id"); }
  try {
    const r = await fetch("https://open.spotify.com/embed/track/" + id, {
      headers: {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}});
    const m = /"audioPreview":\{"url":"([^"]+)"/.exec(await r.text());
    if (!m) { res.statusCode = 404; return res.end("no clip"); }
    res.setHeader("Cache-Control", "public, max-age=604800, s-maxage=604800");
    res.statusCode = 302;
    res.setHeader("Location", m[1]);
    res.end();
  } catch (e) { res.statusCode = 502; res.end("unavailable"); }
};
