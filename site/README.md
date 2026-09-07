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
- `#/order` — pick a kit (Single / Standard / Extended)
- `#/kit/<id>` — configure that kit's per-strand density, add to cart
- `#/cart` — review cart, adjust quantities
- `#/buy` — checkout (contact info or full order details; no card fields, see below)

## Known limitation: the order form

The "place order" flow on `#/buy` and the live order count/ticker are wired to
Claude's artifact `db` capability (`window.claude.use('db')`), which only exists
when this page is rendered inside a Claude artifact viewer. Outside of that (this
repo, Vercel, GitHub Pages, anywhere else), `claude` is undefined, the code catches
that and shows "ordering isn't available in this view yet" — browsing kits, the
cart, the carousel, and the video all still work fully, only the final submit step
degrades.

If you want orders to actually persist when hosted outside Claude, that call needs
replacing with a real backend (a form endpoint, a serverless function, etc.).

## Deploying

Was deployed ad hoc via `vercel deploy --temporary` (anonymous, expires in 60
minutes unless claimed). For a permanent, auto-deploying setup, link this repo to
Vercel:

```bash
vercel link --repo
git push
```
