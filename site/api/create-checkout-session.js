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
    var densities = Array.isArray(it.densities) ? it.densities.join(', ') : '';
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

  var origin = 'https://' + req.headers.host;

  try {
    var session = await stripe.checkout.sessions.create({
      mode: 'payment',
      payment_method_types: ['card'],
      line_items: line_items,
      shipping_address_collection: { allowed_countries: ['US'] },
      customer_email: email,
      success_url: origin + '/?paid=1&session_id={CHECKOUT_SESSION_ID}#/buy',
      cancel_url: origin + '/#/buy',
      metadata: { name: name, team: team, note: note }
    });
    res.status(200).json({ url: session.url });
  } catch (e) {
    res.status(502).json({ error: 'Could not start checkout.' });
  }
};
