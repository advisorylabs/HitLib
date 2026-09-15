# HitLib - release page

Static site announcing HitLib v1.4.0 / Pattern Studio 1.1.0. Plain HTML/CSS/JS,
no build step, no dependencies to install - open it directly or serve the folder
with any static file server.

## Structure

- `index.html` - markup only. Links out to the CSS and JS below and loads real
  images from `assets/` (no inlined `data:` URIs).
- `css/styles.css` - all page styles.
- `js/app.js` - **the actual application code**: routing (`#/`, `#/order`,
  `#/kit/:id`, `#/cart`, `#/buy`), the kit/density catalog, cart logic, the
  hero carousel, scroll-reveal wiring, and the homepage's LED demos (small
  browser ports of HitLib's animations, the gauge and mode-stack demos, and the
  PROS/VEXcode code-sample toggle). This is the file to read if you want
  to understand or change how the site behaves.
- `js/vendor/` - vendored copies of GSAP and ScrollTrigger (unmodified,
  fetched from the official CDN release), used for the scroll animations.
- `assets/` - every image and the launch video, as real files.

This mirrors a version of the page that also runs standalone inside a Claude
artifact, where everything gets inlined into one file for that platform's
constraints - same code, packaged differently for that context.

## Running locally

```bash
python3 -m http.server 8000
```

then open `http://localhost:8000`.

## Routing

It's a single-page app using hash routing, all client-side, no server needed:

- `#/` - home
- `#/order` - pick a kit (Single / Regular / Extended)
- `#/kit/<id>` - configure that kit's per-strand density, add to cart
- `#/cart` - review cart, adjust quantities
- `#/buy` - checkout: shipping address plus live shipping quote, then "Contact me
  later" for Zelle/Venmo via Discord, or "Pay with card" via Stripe Checkout (no
  card fields on this site itself, see below)

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

## Card payments (Stripe)

"Pay with card" POSTs to `/api/create-checkout-session`, which creates a Stripe
Checkout Session server-side (kit prices come from a trusted map in that file,
never from the client) and redirects the buyer to Stripe's own hosted payment
page - card number and CVC are entered there, never in a field on this site.
The shipping address is the exception: it's entered on `#/buy` so shipping can
be quoted before checkout (see [Shipping](#shipping-shippo) below), and it's
passed to Stripe along with the shipping charge. Stripe redirects back to `#/buy` afterward, where the page
calls `/api/verify-session` to confirm the session actually paid before showing
the confirmation screen and clearing the cart.

The real source of truth for "did this get paid" is `api/stripe-webhook.js`,
which Stripe calls server-to-server on `checkout.session.completed` (verified
via a signing secret, so nobody can fake this by hitting the endpoint directly)
and which sends the Discord order notification - the same pattern as
`api/order.js` above, reusing `DISCORD_WEBHOOK_URL`.

Needs three things set up in the Vercel project, and `npm install` locally
(there's now a `package.json` for the `stripe` dependency):

1. A Stripe account (test mode is the default for a brand new account, no
   business verification needed to start testing - use it first).
2. `STRIPE_SECRET_KEY` - Developers -> API keys in the Stripe dashboard.
3. A webhook endpoint pointing at `https://<your-domain>/api/stripe-webhook`,
   listening for `checkout.session.completed` (Developers -> Webhooks -> Add
   endpoint) - then `STRIPE_WEBHOOK_SECRET` from that endpoint's signing secret.

Test with Stripe's published test card `4242 4242 4242 4242`, any future
expiry, any CVC. Without `STRIPE_SECRET_KEY` set, `/api/create-checkout-session`
returns a 500 and the form tells the buyer to use "Contact me later" instead;
everything else on the site keeps working.

## Shipping (Shippo)

Shipping is a live carrier rate. As the buyer fills in their address on `#/buy`,
the page POSTs the cart and address to `/api/shipping-quote`, which asks
[Shippo](https://goshippo.com) for rates and shows the cheapest one in the
order summary. That quote is display-only. `/api/create-checkout-session` and
`/api/order` re-quote on the server with the same code (`api/_shipping.js`;
the underscore keeps Vercel from routing it), so the charged amount never
comes from the browser.

Stripe's hosted Checkout page can't recalculate shipping after an address is
typed there, which is why the address is collected on our page. The checkout
session gets the rate as a fixed-amount `shipping_options` entry, and the
address as `payment_intent_data.shipping` plus `ship_to` metadata. Buyers can't
change the address on Stripe's page, so the quote always matches it. The
Discord notification (from `api/stripe-webhook.js`) shows the address, the
shipping charge, and the carrier service.

On "Contact me later", the address is required too, and the notification
includes the quoted shipping. Nothing is charged on that path, so if the carrier
lookup fails the order still goes through, marked "Not quoted", for the team to
price by hand. Inside a Claude artifact there's no `/api`, so the address
fields are hidden and shipping is left to the team. That also keeps home
addresses out of the artifact's shared `orders` collection.

Every order ships in one 7" x 9" padded envelope, however many kits are in the
cart, so there's one shipping charge per order. Weight is each strip's maximum
weight (30 LEDs/m 0.6 oz; 60 and 74 LEDs/m 0.4 oz) plus 1 oz of packaging. These
values, and the envelope size, are at the top of `api/_shipping.js`; update
them there if packaging changes. Envelope thickness is quoted as 2" to be safe.

Orders of more than 5 strips (`MAX_STRIPS_PER_ENVELOPE`) don't get a live quote.
Card checkout refuses them with a note to use "Contact me later", and those
orders reach Discord marked "Not quoted" so the team can price shipping by hand.
For reference: one Extended kit (4) or an Extended plus a Single (5) still
quotes; three Regular kits (6) don't.

Buyers can also tick **shipping protection** (+$2.00, unchecked by default),
which covers packages lost or stolen in transit. Damage in shipping is covered
on every order either way. The price is `PROTECTION_CENTS` in `api/_shipping.js`,
with display copies in `js/app.js` and the checkbox label in `index.html`. On
card orders it's a separate Stripe line item. Both Discord notifications show
a "Shipping protection" field, so the team knows which orders to replace or
refund if a package goes missing.

Setup:

1. Create a Shippo account. Its test API token (`shippo_test_...`, Settings ->
   API) returns real-looking rates without buying labels. Switch to the live
   token for production.
2. `SHIPPO_API_TOKEN` - that token.
3. The ship-from address, which every quote is priced from: `SHIP_FROM_NAME`,
   `SHIP_FROM_STREET1`, `SHIP_FROM_STREET2` (optional), `SHIP_FROM_CITY`,
   `SHIP_FROM_STATE` (two-letter), `SHIP_FROM_ZIP`. These are env vars rather
   than code so the address stays out of this public repo.

Without these, quotes fail with "Shipping rates are not configured yet", card
checkout is refused, and "Contact me later" orders go through unquoted.

## Order goal and stock counters

The bar across the top of every page shows the order goal (orders placed out of
the target) and how many strands of each density are in stock. The page loads
the numbers from `/api/inventory`. Wherever that route doesn't exist (a local
static server, a Claude artifact), the bar stays hidden. The stock numbers are
display only. Checkout doesn't block an order for more strands than are in stock.

The counters are stored as metadata on one inactive Stripe product with the ID
`hitlib_site_counters`, named "HitLib site counters". It's created on first use
with the starting values in `SEED` at the top of `api/_inventory.js`. Test mode
and live mode each get their own copy, so check the live one's numbers after
switching keys.

**Changing them by hand** (restocks, a new goal, a cancelled order): in the
Stripe dashboard, open Product catalog, find "HitLib site counters" (it's
archived, so it may be under the archived filter), and edit its metadata:

| Key | Meaning |
| --- | --- |
| `orders_placed` | orders so far |
| `order_goal` | the target |
| `stock_d30`, `stock_d60`, `stock_d74` | strands on hand per density |

Changing them takes a Stripe dashboard login, so there's no admin page or
passcode in this public repo. The page picks up edits within about 10 seconds.
That's the edge cache on `/api/inventory`.

**Automatic updates:** every order adds 1 to `orders_placed` and subtracts its
strands from each density's stock, never going below 0:

- Card orders are counted in `api/stripe-webhook.js` once Stripe confirms
  payment. `api/create-checkout-session.js` stores the cart in the session's
  `items` metadata for this. The session is then flagged `inventory_counted`, so
  a redelivered webhook doesn't count it twice. Abandoned checkouts never count.
- "Contact me later" orders are counted in `api/order.js` as soon as they reach
  Discord. They're unpaid and anyone can submit one, so if one falls through,
  add its strands back and lower `orders_placed` in the dashboard.

A count that fails never blocks the order itself. Both paths use
`STRIPE_SECRET_KEY`, so without it there are no counters and the bar stays hidden.

## Deploying

This folder lives inside the main `advisorylabs/HitLib` repository, alongside the
C++ library and the Doxygen docs. Only this subdirectory is deployed.

Import the repo into Vercel once, and in **Project Settings -> Build & Deployment**
set **Root Directory** to `site`. Everything else stays on Vercel's zero-config
defaults: no build command (the page is plain HTML/CSS/JS), the folder itself is
the output directory, `npm install` picks up `stripe` from the `package.json`
here, and each file in `api/` becomes a Node serverless function at `/api/<name>`.
After that every push to `main` that touches `site/` redeploys automatically.

Set these environment variables in the Vercel project (all described above):

| Variable | Used by | Without it |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | `api/order.js`, `api/stripe-webhook.js` | `/api/order` 500s; the form shows a generic error |
| `STRIPE_SECRET_KEY` | `api/create-checkout-session.js`, `api/verify-session.js`, `api/_inventory.js` | card payment 500s; buyers fall back to "Contact me later"; no order goal/stock bar |
| `STRIPE_WEBHOOK_SECRET` | `api/stripe-webhook.js` | paid orders never reach Discord |
| `SHIPPO_API_TOKEN` | `api/_shipping.js` | no shipping quotes; card checkout refused, contact orders go through unquoted |
| `SHIP_FROM_NAME`, `SHIP_FROM_STREET1`, `SHIP_FROM_STREET2` (optional), `SHIP_FROM_CITY`, `SHIP_FROM_STATE`, `SHIP_FROM_ZIP` | `api/_shipping.js` | same as missing `SHIPPO_API_TOKEN` |

Point the Stripe webhook endpoint at `https://<your-domain>/api/stripe-webhook`.

The library's GitHub Pages site (the Doxygen API reference at
<https://advisorylabs.github.io/HitLib/>) is a separate deployment and is not
affected by anything in this folder.
