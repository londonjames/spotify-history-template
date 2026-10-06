// A page for a link to land on, so that a link to one artist and a link to
// Band of Horses do not arrive in a message looking identical.
//
// The explore page keeps its state in the hash, and a hash never reaches a
// server, so nothing on the other end of a shared link could ever say what the
// link was about. This function sits under the tidy path instead: it looks the
// subject up in a summary built at publish time, answers with a small document
// carrying the real title and description, and sends a person on to the page.
// Crawlers stop at the tags. No listening data passes through here, and nothing
// here calls a paid service.
//
// CommonJS on purpose. The runtime compiles ESM to CommonJS, which leaves
// import.meta.url undefined and took the first version of this file down;
// require of the JSON is also what makes the bundler ship it alongside.
const summary = require("./summary.json");

const KINDS = { a: "Artist", t: "Song", l: "Album", p: "Podcast", g: "Person", e: "Episode", d: "Day" };

const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");

// The same spelling the page uses, so a copied link and a generated one match.
const slug = (s) => encodeURIComponent(String(s == null ? "" : s)).replace(/%20/g, "+");
const unslug = (s) => {
  try { return decodeURIComponent(String(s).replace(/\+/g, "%20")); }
  catch { return String(s); }
};

module.exports = function handler(req, res) {
  const q = req.query || {};
  const k = String(q.k || "").slice(0, 1);
  const name = unslug(q.n || "").slice(0, 200);
  const sub = unslug(q.s || "").slice(0, 200);

  const hash = KINDS[k] && name
    ? "#/" + [k, slug(name), ...(sub ? [slug(sub)] : [])].join("/")
    : "#/";
  const path = KINDS[k] && name
    ? "/" + [k, slug(name), ...(sub ? [slug(sub)] : [])].join("/")
    : "/";

  const key = k + "\u0000" + name.toLowerCase() + "\u0000" + sub.toLowerCase();
  const found = summary[key];

  // Anything not in the summary is not a subject here, and answering 200 with
  // its name in the title made every typo and every invented band preview as
  // though it were real. It 404s, says nothing about itself, and claims no
  // canonical -- while still offering the page, because a rare subject the
  // summary skipped is a real page even when this function cannot describe it.
  const title = found ? found.t : "Listening History";
  const desc = found ? found.d
    : "Sixteen years of one Spotify account, every play of it.";

  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Cache-Control", "public, max-age=0, s-maxage=86400, must-revalidate");
  res.status(found ? 200 : 404).send(`<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>${esc(title)}</title>
<meta name="description" content="${esc(desc)}">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#1c1c1c">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Listening">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(desc)}">
${found ? `<meta property="og:url" content="${esc("https://" + req.headers.host + path)}">` : ""}
${found && found.i ? `<meta property="og:image" content="${esc(found.i)}">
<meta name="twitter:image" content="${esc(found.i)}">` : ""}
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="${esc(title)}">
<meta name="twitter:description" content="${esc(desc)}">
${found ? `<link rel="canonical" href="${esc("https://" + req.headers.host + path)}">` : `<meta name="robots" content="noindex">`}
<meta http-equiv="refresh" content="0; url=/${esc(hash)}">
<style>html{background:#1c1c1c;color:#999;font:14px system-ui;padding:2rem}
a{color:#5ea6ff}</style>
</head><body>
<p>${esc(title)}</p>
<p><a href="/${esc(hash)}">Open it</a></p>
<script>location.replace("/" + ${JSON.stringify(hash)});</script>
</body></html>`);
};
