function clean(v, max) {
  return String(v == null ? '' : v)
    .slice(0, max || 200)
    .replace(/@(everyone|here)/gi, '@​$1')
    .replace(/<@/g, '<​@');
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  var webhookUrl = process.env.DISCORD_WEBHOOK_URL;
  if (!webhookUrl) {
    res.status(500).json({ error: 'Ordering is not configured yet.' });
    return;
  }

  var body = req.body || {};
  var name = clean(body.name, 40);
  var method = body.method === 'card' ? 'card' : 'contact';
  var items = Array.isArray(body.items) ? body.items.slice(0, 10) : [];
  var total = typeof body.total === 'number' && isFinite(body.total) ? body.total : 0;
  var note = clean(body.note, 80);

  if (!name || items.length === 0) {
    res.status(400).json({ error: 'Missing name or items.' });
    return;
  }

  var contactLine;
  if (method === 'card') {
    var email = clean(body.email, 80);
    var team = clean(body.team, 20);
    var address = clean(body.address, 120);
    if (!email || !team || !address) {
      res.status(400).json({ error: 'Missing order details.' });
      return;
    }
    contactLine = 'Email: ' + email + '\nTeam: ' + team + '\nAddress: ' + address;
  } else {
    var contact = clean(body.contact, 80);
    if (!contact) {
      res.status(400).json({ error: 'Missing contact.' });
      return;
    }
    contactLine = 'Discord/email: ' + contact;
  }

  var itemLines = items.map(function (it) {
    var densities = Array.isArray(it.densities) ? it.densities.join(', ') : '';
    var qty = Number(it.qty) || 1;
    var unitPrice = Number(it.unitPrice) || 0;
    return '- ' + clean(it.name, 30) + ' x' + qty + ' ($' + unitPrice.toFixed(2) + ' each) [' + clean(densities, 60) + ']';
  }).join('\n');

  var embed = {
    title: 'New HitLib order',
    color: 0xec4899,
    fields: [
      { name: 'Name', value: name, inline: true },
      { name: 'Method', value: method === 'card' ? 'Full order details' : 'Contact me later', inline: true },
      { name: 'Total', value: '$' + total.toFixed(2), inline: true },
      { name: 'Contact', value: contactLine },
      { name: 'Items', value: itemLines || 'none' }
    ],
    timestamp: new Date().toISOString()
  };
  if (note) embed.fields.push({ name: 'Note', value: note });

  try {
    var resp = await fetch(webhookUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ embeds: [embed], allowed_mentions: { parse: [] } })
    });
    if (!resp.ok) {
      res.status(502).json({ error: 'Discord rejected the notification.' });
      return;
    }
  } catch (e) {
    res.status(502).json({ error: 'Could not reach Discord.' });
    return;
  }

  res.status(200).json({ ok: true });
};
