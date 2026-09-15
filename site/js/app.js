
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
      .from('.hero-meta .team-id', { opacity: 0, y: 10 }, '-=0.55')
      .from('.hero-meta .release-chip', { opacity: 0, y: 8, stagger: 0.06 }, '-=0.5')
      .from('.hero h1', { opacity: 0, y: 16 }, '-=0.5')
      .from('.hero .lede', { opacity: 0, y: 14 }, '-=0.6')
      .from('.hero .cta-row .btn', { opacity: 0, y: 12, stagger: 0.08 }, '-=0.5');

    revealEls.forEach(function (el) {
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

  /* ---------- platform toggle (PROS / VEXcode code samples) ---------- */

  var PLATFORM_KEY = 'hitlib_platform';

  function setPlatform(platform) {
    document.querySelectorAll('[data-platform]').forEach(function (btn) {
      var on = btn.getAttribute('data-platform') === platform;
      btn.classList.toggle('active', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    document.querySelectorAll('[data-platform-pane]').forEach(function (pane) {
      pane.hidden = pane.getAttribute('data-platform-pane') !== platform;
    });
  }

  var savedPlatform = null;
  try { savedPlatform = window.localStorage.getItem(PLATFORM_KEY); } catch (e) {}
  setPlatform(savedPlatform === 'vexcode' ? 'vexcode' : 'pros');

  document.querySelectorAll('[data-platform]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var platform = btn.getAttribute('data-platform');
      setPlatform(platform);
      try { window.localStorage.setItem(PLATFORM_KEY, platform); } catch (e) {}
      if (hasGsap && typeof ScrollTrigger !== 'undefined') ScrollTrigger.refresh();
    });
  });

  /* ---------- LED demos ---------- */

  // Small browser ports of HitLib's animations, driven by the arguments shown
  // next to each strip. A demo tick is slower than the library's 20 ms default
  // so the motion is easy to follow. Strips only repaint while on screen.
  var DEMO_TICK_MS = 70;
  var TRACK_RGB = [27, 23, 43]; // --track, the unlit LED body

  function rgbOf(c) { return [(c >> 16) & 255, (c >> 8) & 255, c & 255]; }
  function packRgb(r, g, b) { return (r << 16) | (g << 8) | b; }
  function mod(a, n) { return ((a % n) + n) % n; }
  function lerpColor(a, b, t) {
    var x = rgbOf(a), y = rgbOf(b);
    return packRgb(
      Math.round(x[0] + (y[0] - x[0]) * t),
      Math.round(x[1] + (y[1] - x[1]) * t),
      Math.round(x[2] + (y[2] - x[2]) * t)
    );
  }
  // channel * pct / 100, as setBrightness() applies it.
  function scaleColor(c, pct) {
    var x = rgbOf(c);
    return packRgb(Math.floor(x[0] * pct / 100), Math.floor(x[1] * pct / 100), Math.floor(x[2] * pct / 100));
  }
  // The integer color wheel rainbow() uses, exactly as led_strand.cpp ships it
  // (its last third blends blue back to red, so there is no yellow).
  function wheel(pos) {
    pos = 255 - (pos & 255);
    if (pos < 85) return packRgb(255 - pos * 3, 0, pos * 3);
    if (pos < 170) { pos -= 85; return packRgb(0, pos * 3, 255 - pos * 3); }
    pos -= 170;
    return packRgb(pos * 3, 0, 255 - pos * 3);
  }
  function msToTicks(ms) { return Math.max(1, Math.round(ms / DEMO_TICK_MS)); }
  function fillFrame(frame, color) {
    for (var i = 0; i < frame.length; i++) frame[i] = color;
  }

  // Each factory returns step(tick, frame), which writes frame.length colors.
  // Like the library, pixel i shows buffer[(i + shift) % size], so a positive
  // speed moves the pattern toward pixel 0 (left on the page).
  var ANIM = {
    solid: function (color) {
      return function (tick, frame) { fillFrame(frame, color); };
    },
    flow: function (c1, c2, speed, seamless) {
      var ramp = [];
      return function (tick, frame) {
        var n = frame.length, i;
        if (ramp.length !== n) {
          ramp = [];
          for (i = 0; i < n; i++) {
            // Seamless runs color1 -> color2 -> back, so the wrap has no edge.
            var t = seamless ? 1 - Math.abs((2 * i) / n - 1) : (n > 1 ? i / (n - 1) : 0);
            ramp.push(lerpColor(c1, c2, t));
          }
        }
        for (i = 0; i < n; i++) frame[i] = ramp[mod(i + tick * speed, n)];
      };
    },
    rainbow: function (speed) {
      return function (tick, frame) {
        var n = frame.length;
        for (var i = 0; i < n; i++) frame[i] = wheel(Math.floor(mod(i + tick * speed, n) * 256 / n));
      };
    },
    pulse: function (color, runLength, speed, bg, bounce) {
      return function (tick, frame) {
        var n = frame.length, i, start;
        fillFrame(frame, bg);
        if (bounce) {
          var range = Math.max(1, n - runLength);
          var p = mod(tick * Math.max(1, speed), 2 * range);
          start = p <= range ? p : 2 * range - p;
          for (i = 0; i < runLength && start + i < n; i++) frame[start + i] = color;
        } else {
          start = mod(-tick * speed, n);
          for (i = 0; i < runLength; i++) frame[(start + i) % n] = color;
        }
      };
    },
    flash: function (color, onMs, offMs, bg) {
      var on = msToTicks(onMs), off = msToTicks(offMs);
      return function (tick, frame) { fillFrame(frame, mod(tick, on + off) < on ? color : bg); };
    },
    twinkle: function (palette, densityPct, fadeStep, bg) {
      var HOLD_TICKS = 8;
      var px = [];
      return function (tick, frame) {
        var n = frame.length, i, active = 0;
        if (px.length !== n) {
          px = [];
          for (i = 0; i < n; i++) px.push({ state: 0, level: 0, hold: 0, color: palette[0] });
        }
        for (i = 0; i < n; i++) if (px[i].state) active++;
        // At most one new sparkle per tick, up to densityPct of the strip.
        if (active < Math.round(n * densityPct / 100)) {
          for (var tries = 0; tries < 6; tries++) {
            var cand = px[Math.floor(Math.random() * n)];
            if (!cand.state) {
              cand.state = 1;
              cand.level = 0;
              cand.color = palette[Math.floor(Math.random() * palette.length)];
              break;
            }
          }
        }
        for (i = 0; i < n; i++) {
          var s = px[i];
          if (s.state === 1) {
            s.level = Math.min(255, s.level + fadeStep);
            if (s.level === 255) { s.state = 2; s.hold = HOLD_TICKS; }
          } else if (s.state === 2) {
            if (--s.hold <= 0) s.state = 3;
          } else if (s.state === 3) {
            s.level = Math.max(0, s.level - fadeStep);
            if (s.level === 0) s.state = 0;
          }
          frame[i] = lerpColor(bg, s.color, s.level / 255);
        }
      };
    },
    // segments: [[color, width], ...], each followed by `spacing` background pixels.
    bitscroll: function (segments, speed, bg, spacing) {
      var unit = [];
      segments.forEach(function (seg) {
        var i;
        for (i = 0; i < seg[1]; i++) unit.push(seg[0]);
        for (i = 0; i < spacing; i++) unit.push(bg);
      });
      unit = unit.slice(0, 64);
      return function (tick, frame) {
        var n = frame.length;
        for (var i = 0; i < n; i++) frame[i] = unit[mod(i + tick * speed, n) % unit.length];
      };
    }
  };

  function LedStrip(el, n) {
    this.dots = [];
    this.last = [];
    this.frame = [];
    el.style.setProperty('--n', n);
    var frag = document.createDocumentFragment();
    for (var i = 0; i < n; i++) {
      var dot = document.createElement('span');
      dot.className = 'led-dot';
      frag.appendChild(dot);
      this.dots.push(dot);
      this.last.push(-1);
      this.frame.push(0);
    }
    el.appendChild(frag);
  }

  LedStrip.prototype.paint = function () {
    for (var i = 0; i < this.dots.length; i++) {
      var c = this.frame[i] & 0xffffff;
      if (c === this.last[i]) continue;
      this.last[i] = c;
      var x = rgbOf(c), dot = this.dots[i];
      if (x[0] + x[1] + x[2] < 3) {
        dot.classList.remove('on');
        continue;
      }
      // Light adds onto the dark LED body, so dim colors still read as lit.
      var r = Math.min(255, TRACK_RGB[0] + x[0]);
      var g = Math.min(255, TRACK_RGB[1] + x[1]);
      var b = Math.min(255, TRACK_RGB[2] + x[2]);
      dot.style.setProperty('--c', 'rgb(' + r + ',' + g + ',' + b + ')');
      dot.style.setProperty('--h', 'rgba(' + x[0] + ',' + x[1] + ',' + x[2] + ',0.45)');
      dot.classList.add('on');
    }
  };

  var demos = [];
  var demoHooks = [];
  var demoTick = 0;

  function addDemo(el, step) {
    var n = parseInt(el.getAttribute('data-n'), 10) || 24;
    var demo = { el: el, strip: new LedStrip(el, n), step: step, visible: false };
    demos.push(demo);
    return demo;
  }

  function refreshDemo(demo) {
    demo.step(demoTick, demo.strip.frame);
    demo.strip.paint();
  }

  var STATIC_DEMOS = {
    solid: function () { return ANIM.solid(0x3AA8FF); },
    flow: function () { return ANIM.flow(0xFF00DD, 0x000000, 1, true); },
    rainbow: function () { return ANIM.rainbow(1); },
    pulse: function () { return ANIM.pulse(0xFF0000, 5, 1, 0x330000, true); },
    flash: function () { return ANIM.flash(0x00FF00, 100, 400, 0x000000); },
    twinkle: function () { return ANIM.twinkle([0xFFFFFF, 0xFFDD88], 30, 16, 0x000000); },
    bitscroll: function () { return ANIM.bitscroll([[0xFF0000, 3], [0x00FF00, 3], [0x0000FF, 3]], 1, 0x000000, 5); },
    // The regions from the docs' spliceMaskCustom example, over a dim purple base.
    zones: function () {
      var base = ANIM.flow(0x3A1670, 0x000000, 1, true);
      var regions = [
        { start: 0, width: 5, step: ANIM.solid(0xFF0000) },
        { start: 10, width: 8, step: ANIM.rainbow(1) },
        { start: 20, width: 6, step: ANIM.pulse(0x00FF00, 3, 2, 0x000000, false) },
        { start: 30, width: 6, step: ANIM.twinkle([0xFFFFFF], 40, 16, 0x000000) },
        { start: 40, width: 8, step: ANIM.bitscroll([[0x00FFAA, 3]], 1, 0x000000, 5) }
      ];
      regions.forEach(function (r) { r.buf = new Array(r.width); });
      return function (tick, frame) {
        base(tick, frame);
        regions.forEach(function (r) {
          r.step(tick, r.buf);
          for (var i = 0; i < r.width && r.start + i < frame.length; i++) frame[r.start + i] = r.buf[i];
        });
      };
    },
    // spliceMask(3, false, true, 400, 0x000000, true): 4 bins trading base and overlay.
    split: function () {
      var base = ANIM.flow(0x9B4DFF, 0x000000, 1, true);
      var overlay = ANIM.rainbow(1);
      var over = [];
      var period = msToTicks(400);
      return function (tick, frame) {
        var n = frame.length, bins = 4;
        if (over.length !== n) over = new Array(n);
        base(tick, frame);
        overlay(tick, over);
        var invert = Math.floor(tick / period) % 2 === 1;
        for (var i = 0; i < n; i++) {
          var bin = Math.min(bins - 1, Math.floor(i * bins / n));
          if ((bin % 2 === 0) !== invert) frame[i] = over[i];
        }
      };
    }
  };

  document.querySelectorAll('.leds[data-demo]').forEach(function (el) {
    var make = STATIC_DEMOS[el.getAttribute('data-demo')];
    if (make) addDemo(el, make());
  });

  /* brightness */

  var brightPct = 60;
  var brightEl = document.querySelector('.leds[data-demo="brightness"]');
  if (brightEl) {
    var brightRainbow = ANIM.rainbow(1);
    var brightDemo = addDemo(brightEl, function (tick, frame) {
      brightRainbow(tick, frame);
      for (var i = 0; i < frame.length; i++) frame[i] = scaleColor(frame[i], brightPct);
    });
    var brightRange = document.getElementById('bright-range');
    var brightCode = document.getElementById('bright-code');
    if (brightRange) {
      brightRange.addEventListener('input', function () {
        brightPct = Math.max(0, Math.min(100, parseInt(brightRange.value, 10) || 0));
        if (brightCode) brightCode.textContent = 'strand.setBrightness(' + brightPct + ');';
        refreshDemo(brightDemo);
      });
    }
  }

  /* music EQ (illustrative loudness per band) */

  function noise(n) {
    var x = Math.sin(n * 12.9898) * 43758.5453;
    return x - Math.floor(x);
  }
  function eqTarget(band, t) {
    var sinceKick = t % 12;
    var kick = Math.pow(0.7, sinceKick);
    if (band === 0) return 0.12 + 0.88 * kick;
    if (band === 1) return 0.4 + 0.2 * Math.sin(t * 0.37) + 0.16 * Math.sin(t * 0.91 + 1);
    if (band === 2) return noise(t) > 0.7 ? 0.5 + 0.45 * noise(t + 7) : 0.1;
    return 0.32 + 0.38 * kick + 0.12 * Math.sin(t * 0.37);
  }
  document.querySelectorAll('.leds[data-demo="eq"]').forEach(function (el) {
    var band = parseInt(el.getAttribute('data-band'), 10) || 0;
    var level = 0;
    addDemo(el, function (tick, frame) {
      var n = frame.length;
      var target = Math.max(0, Math.min(1, eqTarget(band, tick)));
      level = target > level ? target : Math.max(target, level - 0.1);
      var fill = level * n, full = Math.floor(fill);
      for (var i = 0; i < n; i++) {
        var c = lerpColor(0x00FF88, 0xFF0044, n > 1 ? i / (n - 1) : 0);
        frame[i] = i < full ? c : i === full ? scaleColor(c, Math.round((fill - full) * 100)) : 0;
      }
    });
  });

  /* motor heat gauges */

  // motorHeatGauge()'s stops: where a V5 motor starts losing power.
  var HEAT_STOPS = [[20, 0x00FF00], [45, 0xFFFF00], [55, 0xFF7000], [60, 0xFF2000], [65, 0xFF0000], [70, 0xFF00FF]];
  var HEAT_MOTORS = [
    { mid: 36, amp: 11, rate: 0.021, phase: 0.0 },
    { mid: 45, amp: 9, rate: 0.017, phase: 1.7 },
    { mid: 31, amp: 7, rate: 0.026, phase: 3.1 },
    { mid: 49, amp: 12, rate: 0.019, phase: 0.6 },
    { mid: 58, amp: 9, rate: 0.015, phase: 2.4 },
    { mid: 41, amp: 13, rate: 0.023, phase: 4.2 }
  ];
  var gaugeStyle = 'heat';

  function heatColor(temp) {
    if (temp <= HEAT_STOPS[0][0]) return HEAT_STOPS[0][1];
    for (var i = 1; i < HEAT_STOPS.length; i++) {
      if (temp <= HEAT_STOPS[i][0]) {
        var a = HEAT_STOPS[i - 1], b = HEAT_STOPS[i];
        return lerpColor(a[1], b[1], (temp - a[0]) / (b[0] - a[0]));
      }
    }
    return HEAT_STOPS[HEAT_STOPS.length - 1][1];
  }
  function motorTemp(i, tick) {
    var m = HEAT_MOTORS[i];
    var t = m.mid + m.amp * Math.sin(tick * m.rate + m.phase) + 1.5 * Math.sin(tick * 0.11 + m.phase * 3);
    return Math.max(20, Math.min(70, t));
  }
  function cssColor(c) {
    var x = rgbOf(c);
    return 'rgb(' + x[0] + ',' + x[1] + ',' + x[2] + ')';
  }

  var heatDemos = [];
  document.querySelectorAll('.leds[data-demo="heat"]').forEach(function (el) {
    var seg = parseInt(el.getAttribute('data-seg'), 10) || 0;
    heatDemos.push(addDemo(el, function (tick, frame) {
      var temp = motorTemp(seg, tick), color = heatColor(temp), n = frame.length;
      if (gaugeStyle === 'heat') {
        fillFrame(frame, color);
        return;
      }
      // Fill Bar: proportional fill with a partly lit edge pixel, each pixel
      // colored by its own place on the scale.
      var fill = (temp - 20) / 50 * n, full = Math.floor(fill);
      for (var i = 0; i < n; i++) {
        var pc = heatColor(20 + 50 * (n > 1 ? i / (n - 1) : 1));
        frame[i] = i < full ? pc : i === full ? scaleColor(pc, Math.round((fill - full) * 100)) : 0;
      }
    }));
  });

  var gaugeRoot = document.getElementById('gauge-demo');
  function updateGaugeReadouts(tick) {
    if (!gaugeRoot) return;
    var labels = gaugeRoot.querySelectorAll('.heat-label b');
    gaugeRoot.querySelectorAll('.motor').forEach(function (g) {
      var i = parseInt(g.getAttribute('data-motor'), 10);
      var temp = motorTemp(i, tick);
      var fill = cssColor(heatColor(temp));
      var text = Math.round(temp) + ' °C';
      g.querySelector('.motor-body').style.fill = fill;
      g.querySelector('.motor-halo').style.fill = fill;
      g.querySelector('.motor-temp').textContent = text;
      if (labels[i]) labels[i].textContent = Math.round(temp) + '°';
    });
  }
  if (gaugeRoot) {
    demoHooks.push(function (tick) { if (tick % 4 === 0) updateGaugeReadouts(tick); });
    gaugeRoot.querySelectorAll('[data-gauge-style]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        gaugeStyle = btn.getAttribute('data-gauge-style');
        gaugeRoot.querySelectorAll('[data-gauge-style]').forEach(function (b) {
          var on = b === btn;
          b.classList.toggle('active', on);
          b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        heatDemos.forEach(refreshDemo);
      });
    });
  }

  /* profile modes */

  // Classic's endgame loops three phases. The demo slows the 120/120 ms flash
  // to about two blinks a second, well under photosensitivity limits.
  var ENDGAME_PHASES = [msToTicks(1500), msToTicks(8500), msToTicks(2000)];
  var ENDGAME_TICKS = ENDGAME_PHASES[0] + ENDGAME_PHASES[1] + ENDGAME_PHASES[2];
  function endgameLook() {
    var phases = [
      ANIM.flash(0xFFFF00, 210, 210, 0x000000),
      ANIM.solid(0xFFFFFF),
      ANIM.pulse(0xFF0000, 5, 1, 0x000000, false)
    ];
    function phaseAt(t) {
      return t < ENDGAME_PHASES[0] ? 0 : t < ENDGAME_PHASES[0] + ENDGAME_PHASES[1] ? 1 : 2;
    }
    var step = function (tick, frame) {
      var t = mod(tick, ENDGAME_TICKS);
      phases[phaseAt(t)](t, frame);
    };
    // The Sequencer only advances while its mode is winning, so once nothing
    // is active the strip stays on whichever phase was running.
    step.freeze = function (tick) {
      var phase = phases[phaseAt(mod(tick, ENDGAME_TICKS))];
      return function (t, frame) { phase(t, frame); };
    };
    return step;
  }

  // hitlib::profiles::classic, by mode index.
  var CLASSIC_MODES = [
    { name: 'Showoff', pri: 15, look: function () { return ANIM.rainbow(1); } },
    { name: 'Idle', pri: 10, look: function () { return ANIM.flow(0xFF00DD, 0x000000, 1, true); } },
    { name: 'Red', pri: 20, look: function () { return ANIM.pulse(0xFF0000, 5, 1, 0x000000, false); } },
    { name: 'Blue', pri: 20, look: function () { return ANIM.pulse(0x0000FF, 5, 1, 0x000000, false); } },
    { name: 'Scoring', pri: 70, look: function () { return ANIM.pulse(0x00FF00, 5, 1, 0x000000, false); } },
    { name: 'Matchloading', pri: 90, look: function () { return ANIM.pulse(0xEED202, 5, 1, 0x000000, false); } },
    { name: 'Endgame', pri: 100, look: endgameLook }
  ];
  var SCORING = 4, ENDGAME = 6;

  var modeStripEl = document.querySelector('.leds[data-demo="mode"]');
  if (modeStripEl) {
    var modeStack = []; // { idx, timed, endMs }, in activation order
    var modeWinner = -1;
    var modeLook = null;
    var modeStartTick = 0;
    var modeExpiry = null;
    var modeCards = document.querySelectorAll('.mode-card');
    var modeReadout = document.getElementById('mode-readout');
    var phasesEl = document.getElementById('phases');
    var phaseHead = document.getElementById('phase-head');

    var modeDemo = addDemo(modeStripEl, function (tick, frame) {
      if (modeLook) modeLook(tick - modeStartTick, frame);
    });

    var findMode = function (idx) {
      for (var i = 0; i < modeStack.length; i++) if (modeStack[i].idx === idx) return i;
      return -1;
    };

    var renderModes = function () {
      var now = Date.now();
      modeStack = modeStack.filter(function (e) { return !(e.timed && e.endMs <= now); });

      // Highest priority wins; on a tie, the entry activated later wins.
      var winner = -1, best = -1;
      modeStack.forEach(function (e) {
        if (CLASSIC_MODES[e.idx].pri >= best) { best = CLASSIC_MODES[e.idx].pri; winner = e.idx; }
      });
      if (winner !== -1 && winner !== modeWinner) {
        modeLook = CLASSIC_MODES[winner].look();
        modeStartTick = demoTick;
      } else if (winner === -1 && modeWinner !== -1 && modeLook && modeLook.freeze) {
        modeLook = modeLook.freeze(demoTick - modeStartTick);
      }
      modeWinner = winner;

      modeCards.forEach(function (card) {
        var idx = parseInt(card.getAttribute('data-mode'), 10);
        var active = findMode(idx) !== -1;
        card.setAttribute('aria-pressed', active ? 'true' : 'false');
        card.classList.toggle('is-winner', idx === winner);
        if (!active) {
          var drain = card.querySelector('.mode-drain');
          drain.style.transition = 'none';
          drain.style.width = '0';
        }
      });

      if (modeReadout) {
        modeReadout.textContent = winner === -1
          ? 'No active mode. The last look keeps playing.'
          : 'On the strip: ' + CLASSIC_MODES[winner].name + ' · priority ' + CLASSIC_MODES[winner].pri;
      }
      if (phasesEl) phasesEl.hidden = winner !== ENDGAME;
      updatePhaseHead();

      clearTimeout(modeExpiry);
      var nextEnd = Infinity;
      modeStack.forEach(function (e) { if (e.timed) nextEnd = Math.min(nextEnd, e.endMs); });
      if (nextEnd !== Infinity) modeExpiry = setTimeout(renderModes, nextEnd - now + 20);

      refreshDemo(modeDemo);
    };

    var updatePhaseHead = function () {
      if (!phaseHead || modeWinner !== ENDGAME) return;
      var t = mod(demoTick - modeStartTick, ENDGAME_TICKS);
      phaseHead.style.left = (t / ENDGAME_TICKS * 100) + '%';
    };
    demoHooks.push(updatePhaseHead);

    modeCards.forEach(function (card) {
      card.addEventListener('click', function () {
        var idx = parseInt(card.getAttribute('data-mode'), 10);
        var at = findMode(idx);
        if (at === -1) modeStack.push({ idx: idx, timed: false, endMs: 0 });
        else modeStack.splice(at, 1);
        renderModes();
      });
    });

    var scoreBtn = document.getElementById('mode-score');
    if (scoreBtn) {
      scoreBtn.addEventListener('click', function () {
        var at = findMode(SCORING);
        // activateModeTimed: a new timed entry, or a fresh deadline for an
        // existing one. A mode that's already on for good stays that way.
        if (at !== -1 && !modeStack[at].timed) return;
        if (at === -1) modeStack.push({ idx: SCORING, timed: true, endMs: Date.now() + 1500 });
        else modeStack[at].endMs = Date.now() + 1500;
        var drain = document.querySelector('.mode-card[data-mode="' + SCORING + '"] .mode-drain');
        if (drain) {
          drain.style.transition = 'none';
          drain.style.width = '100%';
          void drain.offsetWidth;
          drain.style.transition = 'width 1.5s linear';
          drain.style.width = '0';
        }
        renderModes();
      });
    }

    modeStack.push({ idx: 1, timed: false, endMs: 0 }); // Idle
    modeStack.push({ idx: 2, timed: false, endMs: 0 }); // Red
    renderModes();
  }

  /* demo loop */

  (function startDemos() {
    if (!demos.length) return;
    if (reduceMotion) {
      // One representative frame each, no motion. Tick 28 lands on the lit
      // half of the flash demo and gives twinkle time to fill in.
      for (demoTick = 0; demoTick <= 28; demoTick++) {
        demos.forEach(function (d) { d.step(demoTick, d.strip.frame); });
      }
      demos.forEach(function (d) { d.strip.paint(); });
      demoHooks.forEach(function (hook) { hook(0); });
      updateGaugeReadouts(demoTick);
      return;
    }

    if (typeof IntersectionObserver !== 'undefined') {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          for (var i = 0; i < demos.length; i++) {
            if (demos[i].el === entry.target) demos[i].visible = entry.isIntersecting;
          }
        });
      }, { rootMargin: '120px 0px' });
      demos.forEach(function (d) { io.observe(d.el); });
    } else {
      demos.forEach(function (d) { d.visible = true; });
    }

    demos.forEach(refreshDemo);
    updateGaugeReadouts(0);

    var lastTs = 0, acc = 0;
    function frame(ts) {
      if (lastTs) acc += Math.min(ts - lastTs, 250);
      lastTs = ts;
      if (acc >= DEMO_TICK_MS) {
        acc %= DEMO_TICK_MS;
        demoTick++;
        demoHooks.forEach(function (hook) { hook(demoTick); });
        for (var i = 0; i < demos.length; i++) {
          if (demos[i].visible) refreshDemo(demos[i]);
        }
      }
      window.requestAnimationFrame(frame);
    }
    window.requestAnimationFrame(frame);
  })();

  /* ---------- density + kit catalog + cart ---------- */

  var DENSITY_IMAGES = {
    d30: 'assets/density-30-leds-per-m.png',
    d60: 'assets/density-60-leds-per-m.png',
    d74: 'assets/density-74-leds-per-m.png'
  };

  // Order goal and strands in stock per density, from /api/inventory (edited in
  // the Stripe dashboard, adjusted by each order; see api/_inventory.js). Null
  // until loaded, or wherever there's no /api, which keeps the bar hidden.
  // Display only: nothing at checkout enforces stock.
  var counters = null;
  var LOW_STOCK = 3;

  var DENSITIES = [
    { id: 'd30', length: '26.2"', ledsPerM: 30 },
    { id: 'd60', length: '13.1"', ledsPerM: 60 },
    { id: 'd74', length: '10.6"', ledsPerM: 74 }
  ];

  var KITS = [
    { id: 'single', name: 'Single', strands: 1, price: 25, blurb: 'One strand. Pick any density.' },
    { id: 'standard', name: 'Regular', strands: 2, price: 40, blurb: 'Two strands. Mix and match densities.' },
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
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
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
  function cartCount(cart) { return cartLines(cart).reduce(function (n, l) { return n + l.qty; }, 0); }
  function sellableDensities(densities) {
    return Array.isArray(densities) && densities.length > 0
      && densities.every(function (id) { return !!getDensity(id); });
  }
  function cartLines(cart) {
    return cart
      .map(function (l) { return { kit: getKit(l.kitId), densities: l.densities, qty: l.qty, key: lineKey(l.kitId, l.densities) }; })
      .filter(function (l) { return l.kit && l.qty > 0 && sellableDensities(l.densities); });
  }
  function cartTotal(cart) {
    return cartLines(cart).reduce(function (sum, l) { return sum + l.kit.price * l.qty; }, 0);
  }

  // Mirrors MAX_STRIPS_PER_ENVELOPE and its message in api/_shipping.js, so an
  // oversized cart is flagged before the buyer fills in an address. The server
  // enforces the limit regardless.
  var MAX_STRIPS_PER_ENVELOPE = 5;
  // Display copy of PROTECTION_CENTS in api/_shipping.js (also in the checkbox
  // label in index.html); the server sets what's actually charged.
  var PROTECTION_PRICE = 2;
  var OVER_LIMIT_MESSAGE ='Orders this large may ship in multiple packages for extra protection against shipping damage, "Contact me later" must be used to have this order forwarded to our team for further processing.';
  function cartOverStripLimit(cart) {
    var strips = cartLines(cart).reduce(function (n, l) { return n + l.kit.strands * l.qty; }, 0);
    return strips > MAX_STRIPS_PER_ENVELOPE;
  }
  function overLimitNoteHtml() {
    return '<div class="shipping-limit-note">' + escapeHtml(OVER_LIMIT_MESSAGE) + '</div>';
  }
  function money(n) { return '$' + n.toFixed(2); }

  function kitPriceHtml(kit, extraStyle) {
    return '<div class="kit-price"' + (extraStyle ? ' style="' + extraStyle + '"' : '') + '>' + money(kit.price) + '</div>';
  }

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
          kitPriceHtml(kit) +
          '<a class="kit-detail-link" href="#/kit/' + kit.id + '">Configure &amp; buy &rarr;</a>' +
        '</div>' +
      '</div>';
  }

  function renderKitGrid() {
    var grid = document.getElementById('kit-grid');
    if (!grid) return;
    grid.innerHTML = KITS.map(kitCardHtml).join('');
  }

  function stockOf(d) {
    return counters ? counters.stock[d.id] || 0 : 0;
  }
  function densityOptionText(d) {
    var text = d.length + ' · ' + d.ledsPerM + ' LEDs/m';
    if (!counters) return text;
    return text + ' · ' + (stockOf(d) > 0 ? stockOf(d) + ' in stock' : 'out of stock');
  }

  function renderGoalBar() {
    var bar = document.getElementById('goal-bar');
    var count = document.getElementById('goal-count');
    var track = document.getElementById('goal-track');
    var fill = document.getElementById('goal-fill');
    var list = document.getElementById('goal-stock-list');
    if (!bar || !count || !track || !fill || !list) return;
    bar.hidden = !counters;
    if (!counters) return;
    var placed = counters.ordersPlaced;
    var goal = counters.orderGoal;
    var pct = goal > 0 ? Math.min(100, Math.round(placed / goal * 100)) : 100;
    count.textContent = placed + ' / ' + goal + ' orders';
    fill.style.width = pct + '%';
    track.setAttribute('aria-valuemin', '0');
    track.setAttribute('aria-valuemax', String(goal));
    track.setAttribute('aria-valuenow', String(placed));
    list.innerHTML = DENSITIES.map(function (d) {
      var n = stockOf(d);
      var cls = n <= 0 ? ' out' : n <= LOW_STOCK ? ' low' : '';
      return '<span class="stock-chip' + cls + '" title="' + d.length + ' strand, ' + (n > 0 ? n + ' in stock' : 'out of stock') + '">' +
        '<span class="density">' + d.ledsPerM + ' LEDs/m</span>' +
        '<span class="qty">' + n + '</span>' +
      '</span>';
    }).join('');
  }

  // `fresh` skips the edge cache, for right after this browser placed an order.
  function loadCounters(fresh) {
    return fetch('/api/inventory' + (fresh ? '?t=' + Date.now() : ''))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.stock) return;
        counters = data;
        renderGoalBar();
        // A kit page rendered before the counters arrived gets its labels
        // filled in place, keeping whatever densities were already picked.
        document.querySelectorAll('.density-select option').forEach(function (opt) {
          var d = getDensity(opt.value);
          if (d) opt.textContent = densityOptionText(d);
        });
      })
      .catch(function () {});
  }

  function strandPickerHtml(index, selectedId) {
    return '' +
      '<div class="strand-picker">' +
        '<label>Strand ' + (index + 1) + '</label>' +
        '<select class="density-select" data-strand-index="' + index + '">' +
          DENSITIES.map(function (d) {
            return '<option value="' + d.id + '"' + (d.id === selectedId ? ' selected' : '') + '>' + escapeHtml(densityOptionText(d)) + '</option>';
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
      container.innerHTML = '<p style="margin-top:24px;">That kit does not exist. <a href="#/">Back home</a>.</p>';
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
        kitPriceHtml(kit, 'font-size:22px; margin-top:16px;') +
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
    html += '<div class="cart-total-row"><span>Subtotal</span><span class="amount">' + money(cartTotal(cart)) + '</span></div>';
    html += cartOverStripLimit(cart)
      ? overLimitNoteHtml()
      : '<div class="shipping-hint">Shipping is calculated at checkout from your address.</div>';
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

  function showBuyConfirm(title, subtitle) {
    var orderForm = document.getElementById('buy-order-form');
    var confirm = document.getElementById('buy-confirm');
    var titleEl = document.getElementById('buy-confirm-title');
    var subtitleEl = document.getElementById('buy-confirm-subtitle');
    var backEl = document.getElementById('buy-confirm-back');
    if (orderForm) orderForm.hidden = true;
    if (confirm) confirm.hidden = false;
    if (titleEl) titleEl.textContent = title || 'Order placed';
    if (subtitleEl) subtitleEl.textContent = subtitle || 'A team member will be with you shortly.';
    if (backEl) backEl.hidden = false;
  }

  function checkStripeReturn() {
    var params = new URLSearchParams(location.search);
    var sessionId = params.get('session_id');
    if (!sessionId) return;

    var orderForm = document.getElementById('buy-order-form');
    var confirm = document.getElementById('buy-confirm');
    var backEl = document.getElementById('buy-confirm-back');
    if (orderForm) orderForm.hidden = true;
    if (confirm) confirm.hidden = false;
    if (backEl) backEl.hidden = true;
    var titleEl = document.getElementById('buy-confirm-title');
    var subtitleEl = document.getElementById('buy-confirm-subtitle');
    if (titleEl) titleEl.textContent = 'Confirming your payment…';
    if (subtitleEl) subtitleEl.textContent = 'One moment.';

    fetch('/api/verify-session?session_id=' + encodeURIComponent(sessionId))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.paid) {
          writeCart([]);
          updateCartBadges();
          showBuyConfirm('Order placed', 'A team member will be with you shortly.');
          // Stripe's webhook, which does the counting, can land a moment after the redirect.
          setTimeout(function () { loadCounters(true); }, 4000);
        } else {
          showBuyConfirm("Couldn't confirm payment", "If you were charged, reach out on Discord and we'll sort it out.");
        }
      })
      .catch(function () {
        showBuyConfirm("Couldn't confirm payment", "If you were charged, reach out on Discord and we'll sort it out.");
      })
      .finally(function () {
        history.replaceState(null, '', location.pathname + location.hash);
      });
  }

  /* ---------- shipping quote ---------- */

  // Latest live carrier quote for the #/buy summary. `key` is the cart and
  // address it was requested for, so a slow stale response can't overwrite a
  // newer one. Display only: the server re-quotes before charging.
  var IDLE_QUOTE = { key: '', status: 'idle', amountCents: 0, service: '', error: '' };
  var shippingQuote = IDLE_QUOTE;
  // Turned off inside a Claude artifact, where there's no /api to quote from.
  var shippingEnabled = true;

  function readShipAddress() {
    function val(id) {
      var el = document.getElementById(id);
      return el ? el.value.trim() : '';
    }
    return {
      name: val('ship-name'),
      street1: val('ship-street1'),
      street2: val('ship-street2'),
      city: val('ship-city'),
      state: val('ship-state'),
      zip: val('ship-zip')
    };
  }
  function shipAddressComplete(a) {
    return !!(a.name && a.street1 && a.city && a.state && /^\d{5}(-\d{4})?$/.test(a.zip));
  }

  function requestShippingQuote() {
    if (!shippingEnabled) return;
    var cart = readCart();
    var lines = cartLines(cart);
    var address = readShipAddress();
    // An oversized cart would only get the limit error back from the carrier call.
    if (!lines.length || !shipAddressComplete(address) || cartOverStripLimit(cart)) {
      if (shippingQuote.status !== 'idle') {
        shippingQuote = IDLE_QUOTE;
        renderBuyTotals();
      }
      return;
    }
    var items = lines.map(function (l) { return { kitId: l.kit.id, densities: l.densities, qty: l.qty }; });
    var key = JSON.stringify([items, address.street1, address.street2, address.city, address.state, address.zip]);
    if (key === shippingQuote.key && shippingQuote.status !== 'error') return;

    function settle(next) {
      if (shippingQuote.key !== key) return;
      shippingQuote = next;
      renderBuyTotals();
    }
    shippingQuote = { key: key, status: 'loading', amountCents: 0, service: '', error: '' };
    renderBuyTotals();
    fetch('/api/shipping-quote', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ items: items, address: address })
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (r.ok && typeof data.amountCents === 'number') {
            settle({ key: key, status: 'ok', amountCents: data.amountCents, service: String(data.service || ''), error: '' });
          } else {
            settle({ key: key, status: 'error', amountCents: 0, service: '', error: data.error || 'Could not get a shipping rate.' });
          }
        });
      })
      .catch(function () {
        settle({ key: key, status: 'error', amountCents: 0, service: '', error: 'Could not reach the shipping desk. Try again in a bit.' });
      });
  }

  function renderBuySummary() {
    var orderForm = document.getElementById('buy-order-form');
    var confirm = document.getElementById('buy-confirm');
    if (orderForm) orderForm.hidden = false;
    if (confirm) confirm.hidden = true;
    return renderBuyTotals();
  }

  // Fills in the summary box only, so a quote landing late can't un-hide the
  // order form behind a confirmation screen.
  function renderBuyTotals() {
    var container = document.getElementById('buy-cart-summary');
    if (!container) return null;
    var cart = readCart();
    var lines = cartLines(cart);
    var overLimit = cartOverStripLimit(cart);
    // Before the empty-cart return, so emptying the cart brings card checkout back.
    syncCardOption(shippingEnabled && overLimit);
    if (!lines.length) {
      container.innerHTML = '<div class="buy-summary"><p style="margin:0; color:var(--text-muted);">Your cart is empty. <a href="#/">Pick a kit</a> before checking out.</p></div>';
      return null;
    }
    var rows = lines.map(function (l) {
      var densityText = l.densities.map(densityLabel).join(', ');
      return '<div class="buy-summary-row"><span>' + l.kit.name + ' &times; ' + l.qty + '<br><span style="font-size:12px; color:var(--text-muted);">' + densityText + '</span></span><span class="amount">' + money(l.kit.price * l.qty) + '</span></div>';
    }).join('');

    var subtotal = cartTotal(cart);
    var total = subtotal;
    var quoted = shippingQuote.status === 'ok' && !overLimit;
    var shipLabel = 'Shipping';
    var shipValue;
    if (!shippingEnabled) {
      shipValue = '<span class="pending">Arranged with the team</span>';
    } else if (overLimit) {
      shipValue = '<span class="pending">Quoted by the team</span>';
    } else if (quoted) {
      total += shippingQuote.amountCents / 100;
      if (shippingQuote.service) shipLabel += '<br><span style="font-size:12px; color:var(--text-muted);">' + escapeHtml(shippingQuote.service) + '</span>';
      shipValue = '<span class="amount">' + money(shippingQuote.amountCents / 100) + '</span>';
    } else if (shippingQuote.status === 'loading') {
      shipValue = '<span class="pending">Calculating…</span>';
    } else if (shippingQuote.status === 'error') {
      shipValue = '<span class="pending">' + escapeHtml(shippingQuote.error) + '</span>';
    } else {
      shipValue = '<span class="pending">Enter your address below</span>';
    }
    var totalLabel = quoted || !shippingEnabled ? 'Total' : 'Total before shipping';

    var protectionInput = document.getElementById('ship-protection');
    var protection = !!(protectionInput && protectionInput.checked);
    if (protection) total += PROTECTION_PRICE;

    container.innerHTML = '<div class="buy-summary">' + rows +
      '<div class="buy-summary-row"><span>Subtotal</span><span class="amount">' + money(subtotal) + '</span></div>' +
      '<div class="buy-summary-row"><span>' + shipLabel + '</span>' + shipValue + '</div>' +
      (protection ? '<div class="buy-summary-row"><span>Shipping protection</span><span class="amount">' + money(PROTECTION_PRICE) + '</span></div>' : '') +
      '<div class="buy-summary-row total"><span>' + totalLabel + '</span><span class="amount">' + money(total) + '</span></div>' +
      (overLimit && shippingEnabled ? overLimitNoteHtml() : '') +
      '</div>';
    return { lines: lines, subtotal: subtotal, total: total, protection: protection };
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
                kitPriceHtml(kit) +
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

  loadCounters();
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
      void led.getBoundingClientRect();
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
    if (route.page === 'buy') {
      renderBuySummary();
      // Covers an address the browser restored, which fires no input event.
      requestShippingQuote();
    }
    if (route.page === 'order') renderOrderWizard();
    if (route.page !== 'home') animatePageIn('#page-' + route.page + ' .module');
    if (route.page === 'home') resetLedStrip();
  }

  showPage(parseRoute());
  checkStripeReturn();

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
    // Each item lights for its own section plus any listed in data-also
    // (sections that sit under it without a rail entry of their own).
    var sections = [];
    var sectionItem = [];
    Array.prototype.forEach.call(railItems, function (item, idx) {
      var ids = [item.getAttribute('data-target')].concat((item.getAttribute('data-also') || '').split(' '));
      ids.forEach(function (id) {
        var el = id && document.getElementById(id);
        if (!el) return;
        sections.push(el);
        sectionItem.push(idx);
      });
    });
    var railObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var at = sections.indexOf(entry.target);
        if (at === -1 || !entry.isIntersecting) return;
        railItems.forEach(function (it) { it.classList.remove('active'); });
        railItems[sectionItem[at]].classList.add('active');
      });
    }, { rootMargin: '-40% 0px -50% 0px', threshold: 0 });
    sections.forEach(function (s) { railObserver.observe(s); });
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
  var noteInput = document.getElementById('reserve-note');
  var submitBtn = form.querySelector('button');
  var statusEl = document.getElementById('reserve-status');
  var countEl = document.getElementById('reserve-count');
  var tickerEl = document.getElementById('reserve-ticker');
  var tickerTrack = document.getElementById('reserve-ticker-track');

  var payMethod = 'card';
  var methodBtns = document.querySelectorAll('.pay-method-btn');
  var contactFields = document.querySelectorAll('.method-contact-field');
  var cardFields = document.querySelectorAll('.method-card-field');
  var contactNote = document.getElementById('method-contact-note');
  var cardNote = document.getElementById('method-card-note');
  var shipFields = document.querySelectorAll('.ship-field');
  var protectionInput = document.getElementById('ship-protection');
  if (protectionInput) protectionInput.addEventListener('change', renderBuyTotals);

  var quoteTimer = null;
  shipFields.forEach(function (f) {
    var input = f.querySelector('input, select');
    if (!input) return;
    ['input', 'change'].forEach(function (type) {
      input.addEventListener(type, function () {
        clearTimeout(quoteTimer);
        quoteTimer = setTimeout(requestShippingQuote, 500);
      });
    });
  });

  function selectPayMethod(method) {
    payMethod = method;
    var isCard = method === 'card';
    methodBtns.forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-method') === method); });
    contactFields.forEach(function (f) { f.hidden = isCard; });
    cardFields.forEach(function (f) { f.hidden = !isCard; });
    if (contactNote) contactNote.hidden = isCard;
    if (cardNote) cardNote.hidden = !isCard;
    submitBtn.textContent = isCard ? 'Continue to payment' : 'Reserve my spot';
    statusEl.textContent = '';
  }

  methodBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      selectPayMethod(btn.getAttribute('data-method'));
    });
  });

  // An oversized cart can't be quoted, so card checkout is removed outright
  // rather than refused on submit. If that forced the switch to "Contact me
  // later", card comes back as the default once the cart is small enough again.
  var cardForcedOff = false;
  function syncCardOption(overLimit) {
    if (!methodBtns) return; // the first render runs before the order form is wired up
    methodBtns.forEach(function (b) {
      if (b.getAttribute('data-method') === 'card') b.hidden = overLimit;
    });
    var toggle = document.getElementById('buy-pay-toggle');
    if (toggle) toggle.classList.toggle('single', overLimit);
    if (overLimit && payMethod === 'card') {
      cardForcedOff = true;
      selectPayMethod('contact');
    } else if (!overLimit && cardForcedOff) {
      cardForcedOff = false;
      selectPayMethod('card');
    }
  }
  syncCardOption(shippingEnabled && cartOverStripLimit(readCart()));

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

    var col = db ? db.collection('orders') : null;

    if (col) {
      // Artifact viewer: no /api to quote from, and the orders collection is
      // readable by other viewers, so no home addresses go into it.
      shippingEnabled = false;
      shipFields.forEach(function (f) { f.hidden = true; });
      renderBuyTotals();
    }

    if (col) {
      col.orderBy('ts', 'desc').limit(50).onSnapshot(function (snap) {
        countEl.innerHTML = '<b>' + snap.size + '</b> ' + (snap.size === 1 ? 'order placed so far' : 'orders placed so far');
        renderTicker(snap);
      }, function (err) {
        countEl.textContent = 'Order count is unavailable right now.';
      });
    } else {
      countEl.hidden = true;
      tickerEl.hidden = true;
    }

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
        subtotal: Math.round(summary.subtotal * 100) / 100,
        total: Math.round(summary.total * 100) / 100,
        protection: summary.protection,
        note: note,
        ts: Date.now()
      };

      var address = readShipAddress();
      var needsAddress = shippingEnabled && !shipAddressComplete(address);
      if (shippingEnabled) payload.address = address;

      if (isCard) {
        payload.email = emailInput.value.trim();
        payload.team = teamInput.value.trim();
        if (shippingEnabled && cartOverStripLimit(readCart())) {
          statusEl.textContent = OVER_LIMIT_MESSAGE;
          return;
        }
        if (!name || !payload.email || !payload.team) {
          statusEl.textContent = 'Fill in your name, email, and team number.';
          return;
        }
        if (needsAddress) {
          statusEl.textContent = 'Fill in your full shipping address.';
          return;
        }

        submitBtn.disabled = true;
        statusEl.textContent = 'Taking you to checkout…';

        try {
          var checkoutResp = await fetch('/api/create-checkout-session', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify(payload)
          });
          var checkoutData = await checkoutResp.json().catch(function () { return {}; });
          if (!checkoutResp.ok || !checkoutData.url) {
            // 400/422 carry a buyer-facing reason (bad address, no carrier, box too big).
            statusEl.textContent = (checkoutResp.status === 400 || checkoutResp.status === 422) && checkoutData.error
              ? checkoutData.error
              : checkoutResp.status === 500
                ? "Card payments aren't set up yet. Try \"Contact me later\" instead."
                : 'Could not start checkout. Try again, or use "Contact me later".';
            submitBtn.disabled = false;
            return;
          }
          window.location.href = checkoutData.url;
        } catch (err) {
          statusEl.textContent = 'Could not reach the payment desk. Try again in a bit.';
          submitBtn.disabled = false;
        }
        return;
      }

      payload.contact = contactInput.value.trim();
      if (!name || !payload.contact) {
        statusEl.textContent = 'Fill in your name and a Discord handle or email.';
        return;
      }
      if (needsAddress) {
        statusEl.textContent = 'Fill in your full shipping address.';
        return;
      }

      submitBtn.disabled = true;
      statusEl.textContent = 'Placing order…';

      try {
        if (col) {
          await col.add(payload);
        } else {
          var resp = await fetch('/api/order', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify(payload)
          });
          if (!resp.ok) {
            var err = new Error('order endpoint failed');
            err.code = resp.status === 429 ? 'resource_exhausted' : resp.status === 400 ? 'invalid_argument' : 'unavailable';
            throw err;
          }
        }
        statusEl.textContent = '';
        writeCart([]);
        updateCartBadges();
        form.reset();
        shippingQuote = IDLE_QUOTE;
        showBuyConfirm();
        if (!col) loadCounters(true);
      } catch (err) {
        var code = err && err.code;
        if (code === 'quota_exceeded') {
          statusEl.textContent = 'Orders are full right now. Try again later.';
        } else if (code === 'resource_exhausted') {
          statusEl.textContent = 'Too many requests. Wait a moment and try again.';
        } else if (code === 'invalid_argument') {
          statusEl.textContent = 'That entry was rejected. Try a shorter name or note.';
        } else if (code === 'unavailable') {
          statusEl.textContent = 'Could not reach the order desk. Try again in a bit, or reach out on Discord.';
        } else {
          statusEl.textContent = 'Something went wrong. Try again.';
        }
      } finally {
        submitBtn.disabled = false;
      }
    });
  })();
})();
