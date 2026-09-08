module.exports = async function handler(req, res) {
  if (req.method !== 'GET') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  if (!process.env.STRIPE_SECRET_KEY) {
    res.status(500).json({ error: 'Card payment is not configured yet.' });
    return;
  }
  var stripe = require('stripe')(process.env.STRIPE_SECRET_KEY);

  var sessionId = req.query && req.query.session_id;
  if (!sessionId || typeof sessionId !== 'string') {
    res.status(400).json({ error: 'Missing session_id' });
    return;
  }

  try {
    var session = await stripe.checkout.sessions.retrieve(sessionId);
    res.status(200).json({ paid: session.payment_status === 'paid' });
  } catch (e) {
    res.status(502).json({ error: 'Could not verify session.' });
  }
};
