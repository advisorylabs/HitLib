
(function () {
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var hasGsap = typeof gsap !== 'undefined';
  var revealEls = document.querySelectorAll('.reveal');

  if (!hasGsap || reduceMotion) {
    revealEls.forEach(function (el) { el.style.opacity = 1; });
  } else {
    gsap.registerPlugin(ScrollTrigger);

    gsap.timeline({ defaults: { ease: 'power3.out', duration: 0.9 } })
      .from('.hero-frame', { opacity: 0, y: 26, duration: 1 })
      .from('.led-strip', { opacity: 0, y: 10 }, '-=0.6')
      .from('.team-id', { opacity: 0, y: 10 }, '-=0.55')
      .from('.hero h1', { opacity: 0, y: 16 }, '-=0.5')
      .from('.hero .lede', { opacity: 0, y: 14 }, '-=0.6')
      .from('.cta-row .btn', { opacity: 0, y: 12, stagger: 0.08 }, '-=0.5');

    revealEls.forEach(function (el) {
      if (el.classList.contains('swatch-strip')) return;
      var children = Array.prototype.filter.call(el.children, function (c) {
        return !c.classList.contains('dot');
      });
      gsap.from(children.length ? children : el, {
        opacity: 0,
        y: 24,
        duration: 0.6,
        ease: 'power2.out',
        stagger: 0.08,
        scrollTrigger: { trigger: el, start: 'top 88%' }
      });
    });

    var swatch = document.querySelector('.swatch-strip');
    if (swatch) {
      gsap.fromTo(swatch,
        { clipPath: 'inset(0% 100% 0% 0% round 6px)' },
        {
          clipPath: 'inset(0% 0% 0% 0% round 6px)',
          duration: 0.8,
          ease: 'power2.out',
          scrollTrigger: { trigger: swatch, start: 'top 92%' }
        }
      );
    }

    var gauge = document.querySelector('.gauge-demo');
    if (gauge) {
      gsap.from(gauge.querySelectorAll('span'), {
        scaleY: 0,
        transformOrigin: 'bottom center',
        duration: 0.6,
        ease: 'power2.out',
        stagger: 0.07,
        scrollTrigger: { trigger: gauge, start: 'top 90%' }
      });
    }

    gsap.set('.hero-frame img', { scale: 1.12 });
    gsap.to('.hero-frame img', {
      yPercent: 8,
      ease: 'none',
      scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: true }
    });
  }

  /* ---------- hero carousel ---------- */

  var heroSlides = Array.prototype.slice.call(document.querySelectorAll('.hero-slide'));
  if (heroSlides.length > 1 && !reduceMotion) {
    var heroIndex = heroSlides.findIndex(function (s) { return s.classList.contains('active'); });
    if (heroIndex === -1) heroIndex = 0;
    setInterval(function () {
      heroSlides[heroIndex].classList.remove('active');
      heroIndex = (heroIndex + 1) % heroSlides.length;
      heroSlides[heroIndex].classList.add('active');
    }, 2000);
  }

  /* ---------- density + kit catalog + cart ---------- */

  var DENSITY_IMAGES = {
    d30: 'assets/density-30-leds-per-m.png',
    d60: 'assets/density-60-leds-per-m.png',
    d74: 'assets/density-74-leds-per-m.png',
    d96: 'assets/density-96-leds-per-m.png',
    d144: 'assets/density-144-leds-per-m.png'
  };

  var DENSITIES = [
    { id: 'd30', length: '26.2"', ledsPerM: 30 },
    { id: 'd60', length: '13.1"', ledsPerM: 60 },
    { id: 'd74', length: '10.6"', ledsPerM: 74 },
    { id: 'd96', length: '8.2"', ledsPerM: 96 },
    { id: 'd144', length: '5.5"', ledsPerM: 144 }
  ];

  var KITS = [
    { id: 'single', name: 'Single', strands: 1, price: 25, blurb: 'One strand. Pick any density.' },
    { id: 'standard', name: 'Standard', strands: 2, price: 40, blurb: 'Two strands. Mix and match densities.' },
    { id: 'extended', name: 'Extended', strands: 4, price: 70, blurb: 'Four strands. Mix and match densities.' }
  ];

  var STRAND_COLORS = ['var(--blue)', 'var(--purple)', 'var(--pink)', 'var(--cyan)'];

  function getKit(id) {
    for (var i = 0; i < KITS.length; i++) if (KITS[i].id === id) return KITS[i];
    return null;
  }
  function getDensity(id) {
    for (var i = 0; i < DENSITIES.length; i++) if (DENSITIES[i].id === id) return DENSITIES[i];
    return null;
  }
  function densityLabel(id) {
    var d = getDensity(id);
    return d ? d.length + ' · ' + d.ledsPerM + ' LEDs/m' : '';
  }
  function defaultDensities(kit) {
    var arr = [];
    for (var i = 0; i < kit.strands; i++) arr.push(DENSITIES[0].id);
    return arr;
  }
  function lineKey(kitId, densities) { return kitId + '::' + densities.join(','); }

  function readCart() {
    try {
      var raw = window.localStorage.getItem('hitlib_cart_v4');
      return raw ? JSON.parse(raw) : [];
    } catch (e) { return []; }
  }
  function writeCart(cart) {
    try { window.localStorage.setItem('hitlib_cart_v4', JSON.stringify(cart)); } catch (e) {}
  }
  function addToCart(kitId, densities, qty) {
    var cart = readCart();
    var key = lineKey(kitId, densities);
    var existing = null;
    for (var i = 0; i < cart.length; i++) {
      if (lineKey(cart[i].kitId, cart[i].densities) === key) { existing = cart[i]; break; }
    }
    if (existing) {
      existing.qty += qty;
    } else {
      cart.push({ kitId: kitId, densities: densities.slice(), qty: qty });
    }
    writeCart(cart);
  }
  function removeCartLine(key) {
    writeCart(readCart().filter(function (l) { return lineKey(l.kitId, l.densities) !== key; }));
  }
  function setCartLineQty(key, qty) {
    var cart = readCart();
    for (var i = 0; i < cart.length; i++) {
      if (lineKey(cart[i].kitId, cart[i].densities) === key) {
        cart[i].qty = Math.max(1, Math.min(20, qty));
        break;
      }
    }
    writeCart(cart);
  }
  function cartCount(cart) { return cart.reduce(function (n, l) { return n + l.qty; }, 0); }
  function cartLines(cart) {
    return cart
      .map(function (l) { return { kit: getKit(l.kitId), densities: l.densities, qty: l.qty, key: lineKey(l.kitId, l.densities) }; })
      .filter(function (l) { return l.kit && l.qty > 0; });
  }
  function cartTotal(cart) {
    return cartLines(cart).reduce(function (sum, l) { return sum + l.kit.price * l.qty; }, 0);
  }
  function money(n) { return '$' + n.toFixed(2); }

  function updateCartBadges() {
    var n = cartCount(readCart());
    document.querySelectorAll('.cart-badge').forEach(function (b) {
      b.textContent = String(n);
      b.hidden = n === 0;
    });
  }

  var KIT_IMAGES = {
    single: 'assets/kit-single.webp',
    standard: 'assets/kit-standard.webp',
    extended: 'assets/kit-extended.webp'
  };

  function kitIconHtml(kit) {
    var src = KIT_IMAGES[kit.id];
    if (src) {
      return '<div class="kit-visual"><img src="' + src + '" alt="' + kit.name + ' kit, ' + kit.strands + ' strand' + (kit.strands > 1 ? 's' : '') + '" /></div>';
    }
    return '<div class="kit-visual"></div>';
  }

  function kitCardHtml(kit) {
    return '' +
      '<div class="kit-card">' +
        kitIconHtml(kit) +
        '<div class="kit-body">' +
          '<div class="kit-name">' + kit.name + '</div>' +
          '<div class="kit-spec">' + kit.strands + ' strand' + (kit.strands > 1 ? 's' : '') + '</div>' +
          '<div class="kit-blurb">' + kit.blurb + '</div>' +
          '<div class="kit-price">' + money(kit.price) + '</div>' +
          '<a class="kit-detail-link" href="#/kit/' + kit.id + '">Configure &amp; buy &rarr;</a>' +
        '</div>' +
      '</div>';
  }

  function renderKitGrid() {
    var grid = document.getElementById('kit-grid');
    if (!grid) return;
    grid.innerHTML = KITS.map(kitCardHtml).join('');
  }

  function strandPickerHtml(index, selectedId) {
    return '' +
      '<div class="strand-picker">' +
        '<label>Strand ' + (index + 1) + '</label>' +
        '<select class="density-select" data-strand-index="' + index + '">' +
          DENSITIES.map(function (d) {
            return '<option value="' + d.id + '"' + (d.id === selectedId ? ' selected' : '') + '>' + d.length + ' &middot; ' + d.ledsPerM + ' LEDs/m</option>';
          }).join('') +
        '</select>' +
        '<div class="density-diagram-frame">' +
          '<img class="density-diagram" src="' + DENSITY_IMAGES[selectedId] + '" alt="' + selectedId + ' spec diagram" />' +
        '</div>' +
      '</div>';
  }

  function renderKitPage(kitId) {
    var kit = getKit(kitId);
    var container = document.getElementById('kit-page-content');
    if (!container) return;
    if (!kit) {
      container.innerHTML = '<p style="margin-top:24px;">That kit does not exist. <a href="#/">Back to the dashboard</a>.</p>';
      return;
    }
    var defaults = defaultDensities(kit);
    var pickers = defaults.map(function (id, i) { return strandPickerHtml(i, id); }).join('');
    container.innerHTML = '' +
      '<div class="module" style="margin-top: 8px;">' +
        '<div class="panel-bar"><span class="dot dot-blue"></span><span class="dot dot-purple"></span><span class="dot dot-pink"></span><span class="slug">// kit/' + kit.id + '</span></div>' +
        '<h2>' + kit.name + '</h2>' +
        '<div class="kit-spec" style="margin-top:6px;">' + kit.strands + ' strand' + (kit.strands > 1 ? 's' : '') + '</div>' +
        '<p style="margin-top:14px; color:var(--text-muted);">' + kit.blurb + ' Pick a density for each strand below, mix and match freely.</p>' +
        '<div class="kit-price" style="font-size:22px; margin-top:16px;">' + money(kit.price) + '</div>' +
        '<div class="strand-picker-list">' + pickers + '</div>' +
        '<div class="kit-add-row" style="margin-top:20px; max-width:320px;">' +
          '<div class="qty-stepper">' +
            '<button type="button" class="qty-dec" aria-label="Decrease quantity">&minus;</button>' +
            '<input type="text" inputmode="numeric" class="qty-value" value="1" aria-label="Quantity" />' +
            '<button type="button" class="qty-inc" aria-label="Increase quantity">+</button>' +
          '</div>' +
          '<button type="button" class="btn primary" id="kit-add-to-cart">Add to cart</button>' +
        '</div>' +
      '</div>';

    var qtyStepper = container.querySelector('.qty-stepper');
    var qtyInput = qtyStepper.querySelector('.qty-value');
    qtyStepper.querySelector('.qty-dec').addEventListener('click', function () {
      qtyInput.value = Math.max(1, (parseInt(qtyInput.value, 10) || 1) - 1);
    });
    qtyStepper.querySelector('.qty-inc').addEventListener('click', function () {
      qtyInput.value = Math.min(20, (parseInt(qtyInput.value, 10) || 1) + 1);
    });

    container.querySelectorAll('.density-select').forEach(function (sel) {
      sel.addEventListener('change', function () {
        var picker = sel.closest('.strand-picker');
        var img = picker.querySelector('.density-diagram');
        img.src = DENSITY_IMAGES[sel.value];
      });
    });

    container.querySelector('#kit-add-to-cart').addEventListener('click', function () {
      var densities = Array.prototype.map.call(container.querySelectorAll('.density-select'), function (s) { return s.value; });
      var qty = Math.max(1, parseInt(qtyInput.value, 10) || 1);
      addToCart(kit.id, densities, qty);
      updateCartBadges();
      window.location.hash = '#/cart';
    });
  }

  function renderCartPage() {
    var container = document.getElementById('cart-page-content');
    if (!container) return;
    var cart = readCart();
    var lines = cartLines(cart);
    if (!lines.length) {
      container.innerHTML = '<div class="cart-empty">Your cart is empty. <a href="#/">Go pick a kit</a>.</div>';
      return;
    }
    var html = lines.map(function (l) {
      var densityText = l.densities.map(densityLabel).join(', ');
      return '' +
        '<div class="cart-line">' +
          kitIconHtml(l.kit) +
          '<div class="info">' +
            '<div class="name">' + l.kit.name + ' (' + l.kit.strands + '-strand)</div>' +
            '<div class="unit">' + money(l.kit.price) + ' each &middot; ' + densityText + '</div>' +
            '<div class="qty-stepper" data-line-key="' + l.key + '" style="margin-top:8px;">' +
              '<button type="button" class="qty-dec" aria-label="Decrease quantity">&minus;</button>' +
              '<input type="text" inputmode="numeric" class="qty-value" value="' + l.qty + '" aria-label="Quantity" />' +
              '<button type="button" class="qty-inc" aria-label="Increase quantity">+</button>' +
            '</div>' +
          '</div>' +
          '<div class="line-total">' + money(l.kit.price * l.qty) + '</div>' +
          '<button type="button" class="cart-remove" data-line-key="' + l.key + '" aria-label="Remove">&times;</button>' +
        '</div>';
    }).join('');
    html += '<div class="cart-total-row"><span>Total</span><span class="amount">' + money(cartTotal(cart)) + '</span></div>';
    html += '<div class="cta-row" style="margin-top:20px;"><a class="btn primary" href="#/buy">Continue to checkout</a></div>';
    container.innerHTML = html;

    container.querySelectorAll('.qty-stepper').forEach(function (stepper) {
      var key = stepper.getAttribute('data-line-key');
      var input = stepper.querySelector('.qty-value');
      function commit(v) {
        setCartLineQty(key, v);
        updateCartBadges();
        renderCartPage();
      }
      stepper.querySelector('.qty-dec').addEventListener('click', function () { commit((parseInt(input.value, 10) || 1) - 1); });
      stepper.querySelector('.qty-inc').addEventListener('click', function () { commit((parseInt(input.value, 10) || 1) + 1); });
    });
    container.querySelectorAll('.cart-remove').forEach(function (btn) {
      btn.addEventListener('click', function () {
        removeCartLine(btn.getAttribute('data-line-key'));
        updateCartBadges();
        renderCartPage();
      });
    });
  }

  function renderBuySummary() {
    var container = document.getElementById('buy-cart-summary');
    if (!container) return null;
    var cart = readCart();
    var lines = cartLines(cart);
    if (!lines.length) {
      container.innerHTML = '<div class="buy-summary"><p style="margin:0; color:var(--text-muted);">Your cart is empty. <a href="#/">Pick a kit</a> before checking out.</p></div>';
      return null;
    }
    var rows = lines.map(function (l) {
      var densityText = l.densities.map(densityLabel).join(', ');
      return '<div class="buy-summary-row"><span>' + l.kit.name + ' &times; ' + l.qty + '<br><span style="font-size:12px; color:var(--text-muted);">' + densityText + '</span></span><span class="amount">' + money(l.kit.price * l.qty) + '</span></div>';
    }).join('');
    var total = cartTotal(cart);
    container.innerHTML = '<div class="buy-summary">' + rows +
      '<div class="buy-summary-row total"><span>Total</span><span class="amount">' + money(total) + '</span></div>' +
      '</div>';
    return { lines: lines, total: total };
  }

  /* ---------- order wizard (kit picker landing) ---------- */

  function renderOrderWizard() {
    var container = document.getElementById('order-wizard-content');
    if (!container) return;
    container.innerHTML = '' +
      '<div class="order-step-label">Which kit?</div>' +
      '<h2 style="margin-top:8px;">Choose a kit to configure</h2>' +
      '<p style="color:var(--text-muted); margin-top:8px;">Each kit is a strand count. You will pick a density for every strand next.</p>' +
      '<div class="order-pick-grid">' +
        KITS.map(function (kit) {
          return '' +
            '<button type="button" class="order-pick-card" data-kit="' + kit.id + '">' +
              kitIconHtml(kit) +
              '<div class="kit-body">' +
                '<div class="kit-name">' + kit.name + '</div>' +
                '<div class="kit-spec">' + kit.strands + ' strand' + (kit.strands > 1 ? 's' : '') + '</div>' +
                '<div class="kit-blurb">' + kit.blurb + '</div>' +
                '<div class="kit-price">' + money(kit.price) + '</div>' +
              '</div>' +
            '</button>';
        }).join('') +
      '</div>';
    container.querySelectorAll('.order-pick-card').forEach(function (btn) {
      btn.addEventListener('click', function () {
        window.location.hash = '#/kit/' + btn.getAttribute('data-kit');
      });
    });
  }

  renderKitGrid();
  updateCartBadges();


  /* ---------- routing: home / buy / kit / cart ---------- */

  var pageHome = document.getElementById('page-home');
  var pageBuy = document.getElementById('page-buy');
  var pageKit = document.getElementById('page-kit');
  var pageCart = document.getElementById('page-cart');
  var pageOrder = document.getElementById('page-order');
  var pages = { home: pageHome, buy: pageBuy, kit: pageKit, cart: pageCart, order: pageOrder };
  var rail = document.getElementById('rail');

  function parseRoute() {
    var h = window.location.hash;
    if (h === '#/buy') return { page: 'buy' };
    if (h === '#/cart') return { page: 'cart' };
    if (h === '#/order') return { page: 'order' };
    var m = h.match(/^#\/kit\/([a-z0-9]+)$/);
    if (m && getKit(m[1])) return { page: 'kit', kitId: m[1] };
    return { page: 'home' };
  }

  function animatePageIn(selector) {
    if (!hasGsap || reduceMotion) return;
    gsap.fromTo(selector, { opacity: 0, y: 16 }, { opacity: 1, y: 0, duration: 0.5, ease: 'power2.out' });
  }

  function resetLedStrip() {
    document.querySelectorAll('.led-strip .led').forEach(function (led) {
      led.style.animation = 'none';
      void led.offsetWidth;
      led.style.animation = '';
    });
  }

  function showPage(route) {
    Object.keys(pages).forEach(function (key) { pages[key].hidden = key !== route.page; });
    if (rail) rail.classList.toggle('is-hidden', route.page !== 'home');
    window.scrollTo(0, 0);
    if (hasGsap && typeof ScrollTrigger !== 'undefined') {
      setTimeout(function () { ScrollTrigger.refresh(); }, 50);
    }
    if (route.page === 'kit') renderKitPage(route.kitId);
    if (route.page === 'cart') renderCartPage();
    if (route.page === 'buy') renderBuySummary();
    if (route.page === 'order') renderOrderWizard();
    if (route.page !== 'home') animatePageIn('#page-' + route.page + ' .module');
    if (route.page === 'home') resetLedStrip();
  }

  showPage(parseRoute());

  window.addEventListener('hashchange', function () {
    showPage(parseRoute());
  });

  document.querySelectorAll('a[href^="#"]').forEach(function (a) {
    var href = a.getAttribute('href');
    if (href.indexOf('#/') === 0) return; // route link, let the hashchange handler take it
    a.addEventListener('click', function (e) {
      var target = document.querySelector(href);
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
    });
  });

  var railItems = document.querySelectorAll('.rail-item');
  if (railItems.length && typeof IntersectionObserver !== 'undefined') {
    var sections = Array.prototype.map.call(railItems, function (item) {
      return document.getElementById(item.getAttribute('data-target'));
    });
    var railObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var idx = sections.indexOf(entry.target);
        if (idx === -1 || !entry.isIntersecting) return;
        railItems.forEach(function (it) { it.classList.remove('active'); });
        railItems[idx].classList.add('active');
      });
    }, { rootMargin: '-40% 0px -50% 0px', threshold: 0 });
    sections.forEach(function (s) { if (s) railObserver.observe(s); });
  }

  /* ---------- orders ---------- */

  function escapeHtml(str) {
    return str.replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var form = document.getElementById('reserve-form');
  var nameInput = document.getElementById('reserve-name');
  var contactInput = document.getElementById('reserve-contact');
  var emailInput = document.getElementById('reserve-email');
  var teamInput = document.getElementById('reserve-team');
  var addressInput = document.getElementById('reserve-address');
  var noteInput = document.getElementById('reserve-note');
  var submitBtn = form.querySelector('button');
  var statusEl = document.getElementById('reserve-status');
  var countEl = document.getElementById('reserve-count');
  var tickerEl = document.getElementById('reserve-ticker');
  var tickerTrack = document.getElementById('reserve-ticker-track');

  var payMethod = 'contact';
  var methodBtns = document.querySelectorAll('.pay-method-btn');
  var contactFields = document.querySelectorAll('.method-contact-field');
  var cardFields = document.querySelectorAll('.method-card-field');
  var contactNote = document.getElementById('method-contact-note');
  var cardNote = document.getElementById('method-card-note');

  methodBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      payMethod = btn.getAttribute('data-method');
      var isCard = payMethod === 'card';
      methodBtns.forEach(function (b) { b.classList.toggle('active', b === btn); });
      contactFields.forEach(function (f) { f.hidden = isCard; });
      cardFields.forEach(function (f) { f.hidden = !isCard; });
      if (contactNote) contactNote.hidden = isCard;
      if (cardNote) cardNote.hidden = !isCard;
      submitBtn.textContent = isCard ? 'Submit order details' : 'Reserve my spot';
      statusEl.textContent = '';
    });
  });

  if (!reduceMotion) tickerEl.classList.add('anim');

  function renderTicker(snap) {
    if (snap.empty) {
      tickerEl.hidden = true;
      return;
    }
    tickerEl.hidden = false;
    var items = snap.docs.map(function (d) {
      var data = d.data() || {};
      var name = String(data.name || 'someone').slice(0, 40);
      return escapeHtml(name) + ' placed an order';
    });
    var dot = '<span class="ticker-dot">·</span>';
    var row = items.map(function (t) { return '<span class="ticker-item">' + t + '</span>'; }).join(dot);
    tickerTrack.innerHTML = tickerEl.classList.contains('anim') ? row + dot + row + dot : row;
  }

  (async function () {
    var db = null;
    try {
      db = await claude.use('db');
    } catch (e) {
      db = null;
    }

    if (!db) {
      countEl.textContent = "Ordering isn't available in this view yet.";
      submitBtn.disabled = true;
      return;
    }

    var col = db.collection('orders');

    col.orderBy('ts', 'desc').limit(50).onSnapshot(function (snap) {
      countEl.innerHTML = '<b>' + snap.size + '</b> ' + (snap.size === 1 ? 'order placed so far' : 'orders placed so far');
      renderTicker(snap);
    }, function (err) {
      countEl.textContent = 'Order count is unavailable right now.';
    });

    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      var name = nameInput.value.trim();
      var note = noteInput.value.trim();
      var isCard = payMethod === 'card';
      var summary = renderBuySummary();

      if (!summary) {
        statusEl.textContent = 'Add a kit to your cart first.';
        return;
      }

      var payload = {
        method: payMethod,
        name: name,
        items: summary.lines.map(function (l) {
          return { kitId: l.kit.id, name: l.kit.name, strands: l.kit.strands, densities: l.densities, qty: l.qty, unitPrice: l.kit.price };
        }),
        total: Math.round(summary.total * 100) / 100,
        note: note,
        ts: Date.now()
      };

      if (isCard) {
        payload.email = emailInput.value.trim();
        payload.team = teamInput.value.trim();
        payload.address = addressInput.value.trim();
        if (!name || !payload.email || !payload.team || !payload.address) {
          statusEl.textContent = 'Fill in your name, email, team number, and address.';
          return;
        }
      } else {
        payload.contact = contactInput.value.trim();
        if (!name || !payload.contact) {
          statusEl.textContent = 'Fill in your name and a Discord handle or email.';
          return;
        }
      }

      submitBtn.disabled = true;
      statusEl.textContent = isCard ? 'Saving order…' : 'Placing order…';

      try {
        await col.add(payload);
        statusEl.textContent = isCard
          ? 'Order details saved. The team will send you a payment link.'
          : 'Order placed. The team will reach out to arrange payment.';
        writeCart({});
        updateCartBadges();
        form.reset();
        renderBuySummary();
      } catch (err) {
        var code = err && err.code;
        if (code === 'quota_exceeded') {
          statusEl.textContent = 'Orders are full right now. Try again later.';
        } else if (code === 'resource_exhausted' || code === 'unavailable') {
          statusEl.textContent = 'Too many requests. Wait a moment and try again.';
        } else if (code === 'invalid_argument') {
          statusEl.textContent = 'That entry was rejected. Try a shorter name or note.';
        } else {
          statusEl.textContent = 'Something went wrong. Try again.';
        }
      } finally {
        submitBtn.disabled = false;
      }
    });
  })();
})();
