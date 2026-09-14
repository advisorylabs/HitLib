function clean(v, max) {
  return String(v == null ? '' : v)
    .slice(0, max || 200)
    .replace(/@(everyone|here)/gi, '@​$1')
    .replace(/<@/g, '<​@');
}

async function buffer(readable) {
  var chunks = [];
  for await (var chunk of readable) {
    chunks.push(typeof chunk === 'string' ? Buffer.from(chunk) : chunk);
  }
  return Buffer.concat(chunks);
}

// What was actually charged, straight from Stripe. A Checkout Session does not
// carry its line items in the webhook payload - they are a separate,
// paginated resource - so they have to be fetched to report them.
async function orderedItems(stripe, sessionId) {
  try {
    var items = await stripe.checkout.sessions.listLineItems(sessionId, { limit: 20 });
    return items.data.map(function (li) {
      var unitCents = li.price && li.price.unit_amount != null
        ? li.price.unit_amount
        : Math.round(li.amount_total / (li.quantity || 1));
      return '- ' + li.description + ' x' + (li.quantity || 1) +
        ' ($' + (unitCents / 100).toFixed(2) + ' each)';
    }).join('\n');
  } catch (e) {
    // The order is already paid; a failed lookup must not cost us the whole
    // notification, so the embed goes out without its item list.
    return '';
  }
}

async function notifyDiscord(stripe, session) {
  var webhookUrl = process.env.DISCORD_WEBHOOK_URL;
  if (!webhookUrl) return;

  var meta = session.metadata || {};
  // Newer API versions moved the collected address under
  // collected_information; older ones put it at the top level.
  var shipping = session.shipping_details
    || (session.collected_information && session.collected_information.shipping_details)
    || session.shipping
    || null;
  // Sessions from create-checkout-session carry the quoted address in
  // metadata (Stripe no longer collects it); the fallbacks cover older ones.
  var address = meta.ship_to
    || (shipping && shipping.address
      ? [shipping.address.line1, shipping.address.line2, shipping.address.city, shipping.address.state, shipping.address.postal_code].filter(Boolean).join(', ')
      : 'n/a');
  var shippingCents = (session.total_details && session.total_details.amount_shipping) || 0;
  var shippingText = '$' + (shippingCents / 100).toFixed(2) + (meta.ship_service ? ' (' + meta.ship_service + ')' : '');

  var embed = {
    title: 'New HitLib order (paid via Stripe)',
    color: 0x2fe0a0,
    fields: [
      { name: 'Name', value: clean(meta.name || (shipping && shipping.name) || 'unknown', 60), inline: true },
      { name: 'Total', value: '$' + (session.amount_total / 100).toFixed(2), inline: true },
      { name: 'Team', value: clean(meta.team || 'n/a', 20), inline: true },
      { name: 'Email', value: clean((session.customer_details && session.customer_details.email) || 'n/a', 80) },
      { name: 'Shipping address', value: clean(address, 200) },
      { name: 'Shipping', value: clean(shippingText, 120) },
      // The charged amount is already in Items, which comes from Stripe's line items.
      { name: 'Shipping protection', value: meta.protection === 'yes' ? 'Yes' : 'No', inline: true },
      { name: 'Items', value: clean(await orderedItems(stripe, session.id), 1000) || 'none' }
    ],
    timestamp: new Date().toISOString()
  };
  if (meta.note) embed.fields.push({ name: 'Note', value: clean(meta.note, 80) });

  try {
    await fetch(webhookUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ embeds: [embed], allowed_mentions: { parse: [] } })
    });
  } catch (e) {}
}

async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).end();
    return;
  }

  var webhookSecret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!webhookSecret || !process.env.STRIPE_SECRET_KEY) {
    res.status(500).end();
    return;
  }
  var stripe = require('stripe')(process.env.STRIPE_SECRET_KEY);

  var event;
  try {
    var buf = await buffer(req);
    var sig = req.headers['stripe-signature'];
    event = stripe.webhooks.constructEvent(buf, sig, webhookSecret);
  } catch (e) {
    res.status(400).send('Webhook signature verification failed.');
    return;
  }

  if (event.type === 'checkout.session.completed') {
    var session = event.data.object;
    if (session.payment_status === 'paid') {
      await notifyDiscord(stripe, session);
    }
  }

  res.status(200).json({ received: true });
}

handler.config = { api: { bodyParser: false } };
module.exports = handler;
