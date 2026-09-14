var shipping = require('./_shipping');

// Trusted server-side prices (cents). Never trust a client-submitted price.
var KIT_PRICES_CENTS = {
  single: 2000,
  standard: 3500,
  extended: 6500
};
var KIT_NAMES = {
  single: 'Single',
  standard: 'Regular',
  extended: 'Extended'
};
// Densities still sold, mirroring DENSITIES in js/app.js. Checked here too:
// a browser holding an older copy of the page can still post a withdrawn one,
// and the order would go to Stripe for a strip we no longer stock.
var SELLABLE_DENSITIES = ['d30', 'd60', 'd74'];

function clean(v, max) {
  return String(v == null ? '' : v).slice(0, max || 200);
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  if (!process.env.STRIPE_SECRET_KEY) {
    res.status(500).json({ error: 'Card payment is not configured yet.' });
    return;
  }
  var stripe = require('stripe')(process.env.STRIPE_SECRET_KEY);

  var body = req.body || {};
  var name = clean(body.name, 40);
  var team = clean(body.team, 20);
  var note = clean(body.note, 80);
  var email = clean(body.email, 80);
  var items = Array.isArray(body.items) ? body.items.slice(0, 10) : [];

  if (!name || !email || items.length === 0) {
    res.status(400).json({ error: 'Missing name, email, or items.' });
    return;
  }

  var line_items = [];
  for (var i = 0; i < items.length; i++) {
    var it = items[i] || {};
    var kitId = String(it.kitId || '');
    var priceCents = KIT_PRICES_CENTS[kitId];
    if (!priceCents) {
      res.status(400).json({ error: 'Unknown kit in cart.' });
      return;
    }
    var qty = Math.max(1, Math.min(20, parseInt(it.qty, 10) || 1));
    var densityIds = Array.isArray(it.densities) ? it.densities : [];
    for (var d = 0; d < densityIds.length; d++) {
      if (SELLABLE_DENSITIES.indexOf(String(densityIds[d])) === -1) {
        res.status(400).json({ error: 'That strip density is no longer available. Reload the page and rebuild your cart.' });
        return;
      }
    }
    var densities = densityIds.join(', ');
    line_items.push({
      price_data: {
        currency: 'usd',
        product_data: {
          name: KIT_NAMES[kitId] + ' kit' + (densities ? ' (' + densities + ')' : '')
        },
        unit_amount: priceCents
      },
      quantity: qty
    });
  }

  // Only a literal true opts in, so a missing or malformed field never adds a charge.
  var protection = body.protection === true;
  if (protection) {
    line_items.push({
      price_data: {
        currency: 'usd',
        product_data: { name: 'Shipping protection (lost or stolen packages)' },
        unit_amount: shipping.PROTECTION_CENTS
      },
      quantity: 1
    });
  }

  // Hosted Checkout can't re-rate shipping once the buyer types an address
  // there, so the address is collected on our page and quoted here, and Stripe
  // gets a fixed shipping amount plus the address it was quoted for.
  var address, quote;
  try {
    address = shipping.parseAddress(body.address);
    quote = await shipping.quoteShipping(items, address);
  } catch (e) {
    if (e instanceof shipping.ShippingError) {
      res.status(e.status).json({ error: e.message });
      return;
    }
    res.status(502).json({ error: 'Could not get a shipping rate.' });
    return;
  }

  var origin = 'https://' + req.headers.host;

  try {
    var session = await stripe.checkout.sessions.create({
      mode: 'payment',
      payment_method_types: ['card'],
      line_items: line_items,
      shipping_options: [{
        shipping_rate_data: {
          type: 'fixed_amount',
          display_name: quote.service.slice(0, 100),
          fixed_amount: { amount: quote.amountCents, currency: 'usd' }
        }
      }],
      payment_intent_data: {
        shipping: {
          name: address.name,
          address: {
            line1: address.street1,
            line2: address.street2 || undefined,
            city: address.city,
            state: address.state,
            postal_code: address.zip,
            country: 'US'
          }
        }
      },
      customer_email: email,
      success_url: origin + '/?paid=1&session_id={CHECKOUT_SESSION_ID}#/buy',
      cancel_url: origin + '/#/buy',
      metadata: {
        name: name,
        team: team,
        note: note,
        ship_to: (address.name + ', ' + shipping.oneLine(address)).slice(0, 500),
        ship_service: quote.service.slice(0, 100),
        protection: protection ? 'yes' : 'no'
      }
    });
    res.status(200).json({ url: session.url });
  } catch (e) {
    res.status(502).json({ error: 'Could not start checkout.' });
  }
};
