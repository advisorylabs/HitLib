// Live carrier shipping quotes via Shippo, shared by shipping-quote.js,
// create-checkout-session.js and order.js. The leading underscore keeps Vercel
// from exposing this file as its own /api route.
//
// Every endpoint that charges or reports shipping calls quoteShipping() itself
// with the cart and address, so the amount charged never comes from the client.

// Strands per kit, mirroring KITS in js/app.js.
var KIT_STRANDS = {
  single: 1,
  standard: 2,
  extended: 4
};

// Every order ships in one 7" x 9" padded envelope. Weights are the measured
// maximums, in ounces, so quotes never come in under the real package.
var STRIP_OZ = {         // per strip, keyed by density id from DENSITIES in js/app.js
  d30: 0.6,
  d60: 0.4,
  d74: 0.4
};
var PACKAGING_OZ = 1;    // envelope and packing material, once per order
var ENVELOPE = { length: 9, width: 7, height: 2 }; // inches; height padded to be safe for coiled strips
// Orders with more strips than this are refused at card checkout and sent to
// "Contact me later", where the team quotes shipping by hand.
var MAX_STRIPS_PER_ENVELOPE = 5;

var US_STATES = [
  'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA', 'HI', 'ID', 'IL',
  'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE',
  'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD',
  'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY'
];

// Carries a buyer-facing message and the HTTP status to answer with.
function ShippingError(message, status) {
  this.message = message;
  this.status = status || 422;
}

function field(v, max) {
  return String(v == null ? '' : v).trim().slice(0, max);
}

// Normalizes a client-submitted US address, or throws ShippingError.
function parseAddress(raw) {
  raw = raw || {};
  var address = {
    name: field(raw.name, 60),
    street1: field(raw.street1, 35),
    street2: field(raw.street2, 35),
    city: field(raw.city, 40),
    state: field(raw.state, 2).toUpperCase(),
    zip: field(raw.zip, 10)
  };
  if (!address.name || !address.street1 || !address.city) {
    throw new ShippingError('Fill in the full shipping address.', 400);
  }
  if (US_STATES.indexOf(address.state) === -1) {
    throw new ShippingError('Pick a US state for the shipping address.', 400);
  }
  if (!/^\d{5}(-\d{4})?$/.test(address.zip)) {
    throw new ShippingError('Enter a valid 5-digit ZIP code.', 400);
  }
  return address;
}

function oneLine(address) {
  return [address.street1, address.street2, address.city, address.state + ' ' + address.zip]
    .filter(Boolean).join(', ');
}

// Weighs the whole order as one padded envelope.
function packParcel(items) {
  var strips = 0;
  var stripOz = 0;
  for (var i = 0; i < items.length; i++) {
    var it = items[i] || {};
    var perKit = KIT_STRANDS[String(it.kitId || '')];
    if (!perKit) throw new ShippingError('Unknown kit in cart.', 400);
    var densities = Array.isArray(it.densities) ? it.densities : [];
    if (densities.length !== perKit) {
      throw new ShippingError('Pick a density for every strand in your kit.', 400);
    }
    var qty = Math.max(1, Math.min(20, parseInt(it.qty, 10) || 1));
    for (var d = 0; d < densities.length; d++) {
      var oz = STRIP_OZ[String(densities[d])];
      if (!oz) {
        throw new ShippingError('That strip density is no longer available. Reload the page and rebuild your cart.', 400);
      }
      stripOz += oz * qty;
    }
    strips += perKit * qty;
  }
  if (strips === 0) throw new ShippingError('Your cart is empty.', 400);
  if (strips > MAX_STRIPS_PER_ENVELOPE) {
    throw new ShippingError('Orders this large may ship in multiple packages for extra protection against shipping damage, "Contact me later" must be used to have this order forwarded to our team for further processing.', 422);
  }

  // Rounded to a tenth so float sums like 0.6 + 0.4 don't reach Shippo as 1.0000000000000002.
  var weightOz = Math.round((stripOz + PACKAGING_OZ) * 10) / 10;
  return {
    length: String(ENVELOPE.length),
    width: String(ENVELOPE.width),
    height: String(ENVELOPE.height),
    distance_unit: 'in',
    weight: String(weightOz),
    mass_unit: 'oz'
  };
}

function shipFrom() {
  var from = {
    name: process.env.SHIP_FROM_NAME,
    street1: process.env.SHIP_FROM_STREET1,
    street2: process.env.SHIP_FROM_STREET2 || '',
    city: process.env.SHIP_FROM_CITY,
    state: process.env.SHIP_FROM_STATE,
    zip: process.env.SHIP_FROM_ZIP,
    country: 'US'
  };
  if (!from.name || !from.street1 || !from.city || !from.state || !from.zip) return null;
  return from;
}

// Resolves to { amountCents, service } for the cheapest carrier rate.
async function quoteShipping(items, address) {
  var token = process.env.SHIPPO_API_TOKEN;
  var from = shipFrom();
  if (!token || !from) {
    throw new ShippingError('Shipping rates are not configured yet.', 500);
  }

  var parcel = packParcel(items);
  var resp;
  try {
    resp = await fetch('https://api.goshippo.com/shipments/', {
      method: 'POST',
      headers: {
        'authorization': 'ShippoToken ' + token,
        'content-type': 'application/json'
      },
      body: JSON.stringify({
        address_from: from,
        address_to: {
          name: address.name,
          street1: address.street1,
          street2: address.street2,
          city: address.city,
          state: address.state,
          zip: address.zip,
          country: 'US'
        },
        parcels: [parcel],
        async: false
      }),
      signal: AbortSignal.timeout(10000)
    });
  } catch (e) {
    throw new ShippingError('Could not reach the shipping carrier. Try again in a bit.', 502);
  }
  if (!resp.ok) {
    throw new ShippingError('Could not get a shipping rate. Try again in a bit.', 502);
  }

  var shipment = await resp.json().catch(function () { return {}; });
  var rates = (shipment.rates || []).filter(function (r) {
    return r.currency === 'USD' && isFinite(parseFloat(r.amount));
  });
  if (!rates.length) {
    throw new ShippingError("No carrier could ship to that address. Double-check it, or use \"Contact me later\".", 422);
  }

  var cheapest = rates.reduce(function (best, r) {
    return parseFloat(r.amount) < parseFloat(best.amount) ? r : best;
  });
  var serviceName = cheapest.servicelevel && cheapest.servicelevel.name;
  return {
    amountCents: Math.round(parseFloat(cheapest.amount) * 100),
    service: [cheapest.provider, serviceName].filter(Boolean).join(' ') || 'Standard shipping'
  };
}

module.exports = {
  ShippingError: ShippingError,
  parseAddress: parseAddress,
  oneLine: oneLine,
  quoteShipping: quoteShipping
};
