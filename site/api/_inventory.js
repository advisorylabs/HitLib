// Order goal and per-density stock counters, shared by inventory.js,
// order.js, create-checkout-session.js and stripe-webhook.js. The leading
// underscore keeps Vercel from exposing this file as its own /api route.
//
// The counters live as metadata on one inactive Stripe product, so the team
// edits them by hand in the Stripe dashboard (Product catalog -> "HitLib site
// counters" -> Metadata). Changing them takes a Stripe login, not a passcode in
// this public repo. Orders adjust the same metadata through the API.

var PRODUCT_ID = 'hitlib_site_counters';

// Density ids from DENSITIES in js/app.js. Each has a `stock_<id>` key.
var DENSITY_IDS = ['d30', 'd60', 'd74'];

// Only used the first time, when the product doesn't exist yet in this Stripe
// mode (test and live keys each get their own copy).
var SEED = {
  orders_placed: '2',
  order_goal: '15',
  stock_d30: '6',
  stock_d60: '5',
  stock_d74: '3'
};

function stripeClient() {
  if (!process.env.STRIPE_SECRET_KEY) return null;
  return require('stripe')(process.env.STRIPE_SECRET_KEY);
}

// Metadata is free text in the dashboard, so anything that isn't a number reads as 0.
function toCount(v) {
  var n = parseInt(v, 10);
  return isFinite(n) && n > 0 ? n : 0;
}

async function getProduct(stripe) {
  try {
    return await stripe.products.retrieve(PRODUCT_ID);
  } catch (e) {
    if (e.statusCode !== 404) throw e;
  }
  try {
    return await stripe.products.create({
      id: PRODUCT_ID,
      name: 'HitLib site counters',
      description: 'Not for sale. Edit this product\'s metadata to change the order goal and stock counts shown on the website. Orders update them automatically.',
      active: false,
      metadata: SEED
    });
  } catch (e) {
    // Another request created it first.
    return await stripe.products.retrieve(PRODUCT_ID);
  }
}

async function readCounters(stripe) {
  var meta = (await getProduct(stripe)).metadata || {};
  var stock = {};
  DENSITY_IDS.forEach(function (id) { stock[id] = toCount(meta['stock_' + id]); });
  return {
    ordersPlaced: toCount(meta.orders_placed),
    orderGoal: toCount(meta.order_goal),
    stock: stock
  };
}

// Strands used per density across a cart of { kitId, densities, qty } items.
function strandsByDensity(items) {
  var used = {};
  (Array.isArray(items) ? items.slice(0, 10) : []).forEach(function (it) {
    it = it || {};
    var qty = Math.max(1, Math.min(20, parseInt(it.qty, 10) || 1));
    (Array.isArray(it.densities) ? it.densities : []).forEach(function (id) {
      id = String(id);
      if (DENSITY_IDS.indexOf(id) !== -1) used[id] = (used[id] || 0) + qty;
    });
  });
  return used;
}

// Counts one order and takes its strands out of stock, flooring at 0. This is
// read-then-write with no lock, so two orders landing in the same instant can
// both read the old numbers; at this volume a hand fix in the dashboard covers it.
async function recordOrder(stripe, items) {
  var meta = (await getProduct(stripe)).metadata || {};
  var used = strandsByDensity(items);
  var update = { orders_placed: String(toCount(meta.orders_placed) + 1) };
  Object.keys(used).forEach(function (id) {
    update['stock_' + id] = String(Math.max(0, toCount(meta['stock_' + id]) - used[id]));
  });
  // Metadata updates merge, so the goal and untouched densities are left alone.
  await stripe.products.update(PRODUCT_ID, { metadata: update });
}

// Compact cart for Checkout Session metadata (values cap at 500 characters),
// e.g. "standard:d30.d60x2;single:d74x1". The webhook decodes it after payment.
function encodeItems(items) {
  return items.map(function (it) {
    return it.kitId + ':' + it.densities.join('.') + 'x' + it.qty;
  }).join(';').slice(0, 500);
}

function decodeItems(str) {
  if (!str) return [];
  return String(str).split(';').map(function (part) {
    var m = part.match(/^([a-z0-9]+):([a-z0-9.]*)x(\d+)$/);
    return m ? { kitId: m[1], densities: m[2] ? m[2].split('.') : [], qty: parseInt(m[3], 10) } : null;
  }).filter(Boolean);
}

module.exports = {
  stripeClient: stripeClient,
  readCounters: readCounters,
  recordOrder: recordOrder,
  encodeItems: encodeItems,
  decodeItems: decodeItems
};
