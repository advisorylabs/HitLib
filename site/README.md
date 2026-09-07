# HitLib — release page

Static site announcing HitLib v1.3.0 / Pattern Studio 1.0.0. Plain HTML/CSS/JS,
no build step, no dependencies to install — open it directly or serve the folder
with any static file server.

## Structure

- `index.html` — markup only. Links out to the CSS and JS below and loads real
  images from `assets/` (no inlined `data:` URIs).
- `css/styles.css` — all page styles.
- `js/app.js` — **the actual application code**: routing (`#/`, `#/order`,
  `#/kit/:id`, `#/cart`, `#/buy`), the kit/density catalog, cart logic, the
  hero carousel, and scroll-reveal wiring. This is the file to read if you want
  to understand or change how the site behaves.
- `js/vendor/` — vendored copies of GSAP and ScrollTrigger (unmodified,
  fetched from the official CDN release), used for the scroll animations.
- `assets/` — every image and the launch video, as real files.

This mirrors a version of the page that also runs standalone inside a Claude
artifact, where everything gets inlined into one file for that platform's
constraints — same code, packaged differently for that context.

## Running locally

```bash
python3 -m http.server 8000
```

then open `http://localhost:8000`.

## Routing

It's a single-page app using hash routing, all client-side, no server needed:

- `#/` — dashboard
- `#/order` — pick a kit (Single / Regular / Extended)
- `#/kit/<id>` — configure that kit's per-strand density, add to cart
- `#/cart` — review cart, adjust quantities
- `#/buy` — checkout (contact info or full order details; no card fields, see below)

## Order notifications

The "place order" flow on `#/buy` tries Claude's artifact `db` capability first
(`window.claude.use('db')`), which only exists inside a Claude artifact viewer and
also drives the live order count/ticker there. Everywhere else (this repo, Vercel,
GitHub Pages), `claude` is undefined, so the form instead POSTs to `/api/order`, a
Vercel serverless function (`api/order.js`) that forwards the order as a Discord
embed via a webhook, so whoever runs the server knows who to DM about payment.

That function needs a `DISCORD_WEBHOOK_URL` environment variable set in the Vercel
project (Project Settings -> Environment Variables, or `vercel env add
DISCORD_WEBHOOK_URL`). Create the webhook from Discord: channel settings -> Integrations
-> Webhooks -> New Webhook, ideally in a channel only the team can see, since anyone
holding that URL can post to it. Without the env var set, `/api/order` returns a
500 and the form shows a generic error; ordering is otherwise unaffected.

## Deploying

Was deployed ad hoc via `vercel deploy --temporary` (anonymous, expires in 60
minutes unless claimed). For a permanent, auto-deploying setup, link this repo to
Vercel:

```bash
vercel link --repo
git push
```
