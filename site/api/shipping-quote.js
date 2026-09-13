var shipping = require('./_shipping');

// Quote shown in the #/buy order summary. Display only: checkout and order
// submission re-quote server-side rather than trusting this number.
module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  var body = req.body || {};
  var items = Array.isArray(body.items) ? body.items.slice(0, 10) : [];

  try {
    var address = shipping.parseAddress(body.address);
    var quote = await shipping.quoteShipping(items, address);
    res.status(200).json({ amountCents: quote.amountCents, service: quote.service });
  } catch (e) {
    if (e instanceof shipping.ShippingError) {
      res.status(e.status).json({ error: e.message });
      return;
    }
    res.status(500).json({ error: 'Could not get a shipping rate.' });
  }
};
