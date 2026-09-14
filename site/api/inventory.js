var inventory = require('./_inventory');

// Public read of the order goal and stock counters for the bar at the top of
// the page. Writes only happen inside the order endpoints (see _inventory.js).
module.exports = async function handler(req, res) {
  if (req.method !== 'GET') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  var stripe = inventory.stripeClient();
  if (!stripe) {
    res.status(500).json({ error: 'Counters are not configured yet.' });
    return;
  }

  try {
    var counters = await inventory.readCounters(stripe);
    // Short edge cache so page views don't each hit Stripe.
    res.setHeader('Cache-Control', 's-maxage=10, stale-while-revalidate=50');
    res.status(200).json(counters);
  } catch (e) {
    res.status(502).json({ error: 'Could not load counters.' });
  }
};
