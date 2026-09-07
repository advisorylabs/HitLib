# HitLib — release page

Single-file static site announcing HitLib v1.3.0 / Pattern Studio 1.0.0. Everything
(styles, scripts, images, video, GSAP) is inlined into `index.html`, so the page has
no build step and no dependencies — open it directly or serve the folder with any
static file server.

## Structure

- `index.html` — the entire site. Self-contained: CSS, JS (including a vendored copy
  of GSAP + ScrollTrigger), and every image/video are inlined as `data:` URIs.
- `assets/` — the original source images and video, extracted back out of
  `index.html` for reference/reuse. These are **not** loaded by the page at
  runtime; they're here so the raw files exist somewhere other than inside one
  giant HTML blob.

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
