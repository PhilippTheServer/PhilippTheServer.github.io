// Pixel repair crew: a worker hammers a brick server apart, rebuilds it, and the
// lights go green. Drawn on <canvas id="pixel-bg"> along the bottom of the viewport.
// Rendered only on pages with `pixel_crew: true` in front matter (issue #43).
(() => {
  const CONFIG = {
    pixelSize: 4,        // CSS px per art pixel
    sceneX: 0.72,        // where the server stands: 0 = left edge, 1 = right edge
    groundOffset: 4,     // art pixels between the ground line and the bottom of the screen
    showGround: false,   // on top of the text a full-width line reads as a strikethrough (#47)
    walkSpeed: 34,       // art pixels per second
    units: 5,            // bricks in the server stack
  };

  const canvas = document.getElementById('pixel-bg');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // Phones get no animation at all, not a hidden one: nothing is drawn and no frame
  // loop runs below this width (issue #45). site.css hides the canvas at the same width.
  const phone = window.matchMedia('(max-width: 699px)');

  const PAL = {
    Y: '#f4c430', y: '#c9981a', S: '#f1c27d', K: '#1b1d24', O: '#e8663d',
    B: '#3a74d8', b: '#2a55a3', D: '#6b4428', G: '#a3aab6',
  };
  const BRICKS = [            // [main, shade, highlight]
    ['#d6453d', '#a3302a', '#ec6b64'],
    ['#3a74d8', '#28529e', '#6497ee'],
    ['#f2bf2c', '#c2921a', '#f8d766'],
    ['#3fa35a', '#2b7640', '#63c27d'],
    ['#8a5cd6', '#6441a6', '#a883ea'],
  ];
  const LED = {
    green: '#5cff8a', greenDim: '#1f6b3a', red: '#ff4d4d', redDim: '#4a1717',
    amber: '#ffb13b', off: '#2a2e38',
  };

  // Worker sprite, 10 px wide, facing right. Rows 0-6 head, 7-11 torso, 12-15 legs.
  const HEAD = [
    '...YYYY...',
    '..YYYYYY..',
    '.yYYYYYYYY',
    '..SSSSSS..',
    '..SSSKSK..',
    '..SSSSSS..',
    '...SSSS...',
  ];
  const TORSO = [
    '..OBOOBO..',
    '..OBBBBO..',
    '..BBBBBB..',
    '..BBBBBB..',
    '..bBBBBb..',
  ];
  const LEGS_STAND = ['..BB..BB..', '..BB..BB..', '..bb..bb..', '..DDD.DDD.'];
  const LEGS_A     = ['..BB..BB..', '.BB....BB.', '.bb....bb.', 'DDD....DDD'];
  const LEGS_B     = ['...BBBB...', '...BBBB...', '...bbbb...', '...DDDDD..'];

  const GLYPHS = {
    '!': ['.X.', '.X.', '.X.', '...', '.X.'],
    '?': ['XX.', '..X', '.X.', '...', '.X.'],
    '♥': ['.X.X.', 'XXXXX', 'XXXXX', '.XXX.', '..X..'],
  };

  let W, H, ps, floorY, serverX, groundColor, builtForWidth;
  let t = 0, state = '', stateT = 0, sub = '', subT = 0, repairIndex = 0, shake = 0;
  let worker, units = [], particles = [], bubble = null;

  const lerp = (a, b, p) => a + (b - a) * p;
  const easeOut = p => 1 - (1 - p) * (1 - p);
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = arr => arr[Math.floor(Math.random() * arr.length)];
  const stackY = i => floorY - 6 * (i + 1);
  const groundY = () => floorY - 6;
  const carryY = () => floorY - 23 + worker.jy;

  function readGroundColor() {
    groundColor = getComputedStyle(document.documentElement).getPropertyValue('--rule').trim() || '#888';
  }

  // ---------- setup ----------
  // The canvas is a strip tall enough for the stack, the flying bricks and the speech
  // bubble, anchored to the bottom. Its height does not depend on the viewport height,
  // so the resize a phone fires when its address bar slides away changes nothing here.
  function layout() {
    ps = CONFIG.pixelSize;
    W = Math.ceil(window.innerWidth / ps);
    H = 6 * CONFIG.units + 48 + CONFIG.groundOffset;
    canvas.width = W;
    canvas.height = H;
    canvas.style.width = W * ps + 'px';
    canvas.style.height = H * ps + 'px';
    floorY = H - CONFIG.groundOffset;
    serverX = W < 220
      ? Math.round(W / 2) - 4
      : Math.round(Math.min(Math.max(W * CONFIG.sceneX, 90), W - 100));
    readGroundColor();
  }

  function buildScene() {
    units = [];
    for (let i = 0; i < CONFIG.units; i++) {
      units.push({
        i, x: serverX, y: stackY(i), vx: 0, vy: 0, fx: 0, fy: 0,
        mode: 'stacked', bounced: false, led: 'green',
        color: BRICKS[i % BRICKS.length], phase: Math.random() * 10,
      });
    }
    worker = { x: -14, jy: 0, vy: 0, dir: 1, arms: 'down', a: 0.9, hammer: 'hand',
               walking: false, walkT: 0, jumps: 0, cheered: false, swingDone: -1 };
    particles = [];
    bubble = null;
    shake = 0;
  }

  function newRound() {
    Object.assign(worker, { x: -14, jy: 0, vy: 0, dir: 1, arms: 'down', hammer: 'hand',
                            jumps: 0, cheered: false, swingDone: -1 });
    units.forEach(u => { u.led = 'green'; });
    const a = Math.floor(Math.random() * units.length);
    let b = Math.floor(Math.random() * units.length);
    if (b === a) b = (a + 2) % units.length;
    units[a].led = 'red';
    units[b].led = 'red';
    setState('enter');
  }

  function setState(s) { state = s; stateT = 0; }
  function say(glyph, dur) { bubble = { g: glyph, life: dur }; }

  // ---------- effects ----------
  function burst(x, y, n, kind) {
    for (let k = 0; k < n; k++) {
      let p;
      if (kind === 'spark') {
        p = { vx: rand(-70, 70), vy: rand(-100, -20), g: 260, life: rand(0.3, 0.7),
              size: 1, color: pick(['#ffe066', '#ffb13b', '#ff7a3b', '#ffffff']), fade: false };
      } else if (kind === 'smoke') {
        p = { vx: rand(-10, 10), vy: rand(-20, -6), g: -4, life: rand(0.8, 1.6),
              size: pick([2, 3]), color: '#8c929e', fade: true };
      } else if (kind === 'dust') {
        p = { vx: rand(-22, 22), vy: rand(-18, -4), g: 40, life: rand(0.25, 0.45),
              size: 1, color: '#9aa0ab', fade: true };
      } else { // click
        const ang = (k / n) * Math.PI * 2;
        p = { vx: Math.cos(ang) * 32, vy: Math.sin(ang) * 32, g: 0, life: 0.22,
              size: 1, color: '#ffffff', fade: false };
      }
      p.x = x; p.y = y; p.max = p.life;
      particles.push(p);
    }
  }

  function impact(k) {
    shake = 1;
    burst(serverX + 2, floorY - 7, 7, 'spark');
    if (k === 2) explode();
  }

  // One landing spot per brick, alternating right and left of the server and 24 art
  // pixels apart, so any number of bricks lands without two sharing a spot.
  function landingSlots(n) {
    const slots = [];
    for (let k = 0; k < n; k++) {
      const step = Math.floor(k / 2);
      slots.push(k % 2 === 0 ? 26 + 24 * step : -48 - 24 * step);
    }
    return slots.sort(() => Math.random() - 0.5);
  }

  function explode() {
    const slots = landingSlots(units.length);
    const g = 260;
    units.forEach((u, idx) => {
      const tx = Math.max(1, Math.min(W - 21, serverX + slots[idx] + Math.round(rand(-3, 3))));
      const vy = -rand(60, 105);
      const dy = groundY() - u.y;
      const flight = (-vy + Math.sqrt(vy * vy + 2 * g * dy)) / g;
      u.vx = (tx - u.x) / flight;
      u.vy = vy;
      u.mode = 'flying';
      u.bounced = false;
      u.led = 'off';
    });
    const cy = floorY - 3 * units.length;
    burst(serverX + 10, cy, 26, 'spark');
    burst(serverX + 10, cy, 12, 'smoke');
    shake = 0;
    worker.vy = -70;
    worker.hammer = 'belt';
    say('!', 1.3);
    setState('explode');
  }

  // ---------- update ----------
  function moveTo(tx, dt) {
    const w = worker;
    const d = tx - w.x;
    const step = CONFIG.walkSpeed * dt;
    if (Math.abs(d) <= step) { w.x = tx; return true; }
    w.dir = Math.sign(d);
    w.x += w.dir * step;
    w.walking = true;
    w.walkT += dt;
    return false;
  }

  function updateRepair(dt) {
    const w = worker;
    const u = units[repairIndex];
    subT += dt;
    if (sub === 'toUnit') {
      w.arms = 'down';
      if (moveTo(u.x + 5, dt)) { sub = 'pickup'; subT = 0; u.fx = u.x; u.fy = u.y; }
    } else if (sub === 'pickup') {
      w.arms = 'up';
      u.mode = 'held';
      const p = Math.min(1, subT / 0.3);
      u.x = lerp(u.fx, w.x - 5, p);
      u.y = lerp(u.fy, carryY(), easeOut(p));
      if (p >= 1) sub = 'toServer';
    } else if (sub === 'toServer') {
      w.arms = 'up';
      const arrived = moveTo(serverX - 13, dt);
      u.x = w.x - 5;
      u.y = carryY();
      if (arrived) { w.dir = 1; sub = 'place'; subT = 0; u.fx = u.x; u.fy = u.y; }
    } else if (sub === 'place') {
      const p = Math.min(1, subT / 0.4);
      w.arms = p < 0.6 ? 'up' : 'down';
      u.x = lerp(u.fx, serverX, p);
      u.y = lerp(u.fy, stackY(u.i), p) - Math.sin(p * Math.PI) * 10;
      if (p >= 1) {
        u.mode = 'stacked';
        u.x = serverX;
        u.y = stackY(u.i);
        burst(serverX + 10, u.y + 3, 6, 'click');
        shake = 0.4;
        repairIndex++;
        if (repairIndex >= units.length) setState('boot');
        else { sub = 'toUnit'; subT = 0; }
      }
    }
  }

  function updateBoot() {
    const w = worker;
    w.arms = w.cheered ? 'up' : 'down';
    units.forEach((u, i) => {
      const on = 0.4 + i * 0.25;
      if (stateT > on + 0.15) u.led = 'green';
      else if (stateT > on) u.led = 'amber';
    });
    const done = 0.4 + units.length * 0.25 + 0.3;
    if (stateT > done && !w.cheered) { w.cheered = true; w.jumps = 2; say('♥', 1.7); }
    if (w.cheered && w.jumps > 0 && w.jy === 0 && w.vy === 0) { w.vy = -65; w.jumps--; }
    if (stateT > done + 1.9) { w.cheered = false; setState('exit'); }
  }

  function update(dt) {
    t += dt;
    stateT += dt;
    const w = worker;
    w.walking = false;

    switch (state) {
      case 'enter':
        w.arms = 'down';
        if (moveTo(serverX - 13, dt)) { w.dir = 1; setState('look'); say('?', 1.2); }
        break;
      case 'look':
        if (stateT > 1.4) { w.swingDone = -1; setState('hit'); }
        break;
      case 'hit': {
        const cyc = 0.75;
        const k = Math.floor(stateT / cyc);
        const p = stateT - k * cyc;
        w.arms = 'swing';
        if (p < 0.35) w.a = lerp(0.9, -1.6, easeOut(p / 0.35));
        else if (p < 0.45) w.a = lerp(-1.6, 0.4, (p - 0.35) / 0.1);
        else w.a = 0.4;
        if (p >= 0.45 && w.swingDone < k) { w.swingDone = k; impact(k); }
        break;
      }
      case 'explode':
        w.arms = stateT < 0.7 ? 'up' : 'down';
        if (stateT > 1.8) { repairIndex = 0; sub = 'toUnit'; subT = 0; setState('repair'); }
        break;
      case 'repair':
        updateRepair(dt);
        break;
      case 'boot':
        updateBoot();
        break;
      case 'exit':
        w.arms = 'down';
        if (moveTo(W + 14, dt)) setState('pause');
        break;
      case 'pause':
        if (stateT > 2.5) newRound();
        break;
    }

    // worker jump physics
    if (w.jy < 0 || w.vy !== 0) {
      w.vy += 320 * dt;
      w.jy += w.vy * dt;
      if (w.jy >= 0) { w.jy = 0; w.vy = 0; }
    }

    // flying bricks
    for (const u of units) {
      if (u.mode !== 'flying') continue;
      u.vy += 260 * dt;
      u.x += u.vx * dt;
      u.y += u.vy * dt;
      if (u.y >= groundY()) {
        u.y = groundY();
        u.x = Math.max(0, Math.min(W - 20, u.x));
        if (!u.bounced) {
          u.bounced = true;
          u.vy = -Math.abs(u.vy) * 0.3;
          u.vx *= 0.4;
          burst(u.x + 10, floorY, 4, 'dust');
        } else {
          u.mode = 'ground';
          u.x = Math.round(u.x);
          u.vx = 0; u.vy = 0;
        }
      }
    }

    // particles
    for (const p of particles) {
      p.vy += p.g * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.life -= dt;
    }
    particles = particles.filter(p => p.life > 0);

    if (bubble) { bubble.life -= dt; if (bubble.life <= 0) bubble = null; }
    shake = Math.max(0, shake - dt * 3);
  }

  // ---------- drawing ----------
  function rect(x, y, w, h, c) { ctx.fillStyle = c; ctx.fillRect(x, y, w, h); }

  function ledColor(u, k) {
    switch (u.led) {
      case 'off': return LED.off;
      case 'amber': return LED.amber;
      case 'red': return Math.floor(t * 4 + k) % 2 ? LED.red : LED.redDim;
      default: return Math.sin(t * (3 + k * 2.3) + u.phase * 6) > -0.2 ? LED.green : LED.greenDim;
    }
  }

  function drawUnit(u, ox) {
    const x = Math.round(u.x) + ox;
    const y = Math.round(u.y);
    const [main, shade, hi] = u.color;
    for (const s of [1, 6, 11, 16]) { rect(x + s, y - 1, 3, 1, main); rect(x + s, y - 1, 1, 1, hi); }
    rect(x, y, 20, 6, main);
    rect(x, y, 20, 1, hi);
    rect(x, y + 5, 20, 1, shade);
    rect(x + 19, y + 1, 1, 4, shade);
    rect(x + 2, y + 2, 10, 2, '#1b1d24');
    for (let k = 1; k < 10; k += 2) rect(x + 2 + k, y + 2, 1, 1, '#3a3f4c');
    rect(x + 14, y + 2, 1, 2, ledColor(u, 0));
    rect(x + 16, y + 2, 1, 2, ledColor(u, 1));
  }

  function drawWorker() {
    const w = worker;
    const ox = Math.round(w.x);
    const oy = Math.round(floorY - 16 + w.jy);
    const P = (c, r, col) => rect(w.dir > 0 ? ox + c : ox + 9 - c, oy + r, 1, 1, col);
    const map = (rows, r0) => rows.forEach((row, ri) => {
      for (let c = 0; c < row.length; c++) if (row[c] !== '.') P(c, r0 + ri, PAL[row[c]]);
    });
    const hammer = (hc, hr, a) => {
      const cx = Math.cos(a), sy = Math.sin(a);
      for (let i = 1; i <= 5; i++) P(hc + Math.round(cx * i), hr + Math.round(sy * i), PAL.D);
      const ex = hc + cx * 6, ey = hr + sy * 6;
      for (let k = -1; k <= 1; k++) for (let th = 0; th <= 1; th++) {
        P(Math.round(ex - sy * k + cx * th), Math.round(ey + cx * k + sy * th), PAL.G);
      }
    };

    let legs = LEGS_STAND;
    if (w.jy < 0) legs = LEGS_A;
    else if (w.walking) legs = Math.floor(w.walkT / 0.14) % 2 ? LEGS_A : LEGS_B;
    map(legs, 12);
    map(TORSO, 7);
    map(HEAD, 0);

    if (w.hammer === 'belt') { P(0, 11, PAL.D); P(0, 12, PAL.D); P(-1, 10, PAL.G); P(0, 10, PAL.G); P(1, 10, PAL.G); }

    const armDown = c => { for (let r = 7; r <= 9; r++) P(c, r, PAL.O); P(c, 10, PAL.S); };
    const armUp = c => { for (let r = 0; r <= 7; r++) P(c, r, PAL.O); P(c, -1, PAL.S); };

    if (w.arms === 'up') { armUp(1); armUp(8); return; }
    armDown(1);
    if (w.arms === 'swing') {
      const cx = Math.cos(w.a), sy = Math.sin(w.a);
      for (let i = 0; i < 3; i++) P(8 + Math.round(cx * i), 7 + Math.round(sy * i), PAL.O);
      const hc = 8 + Math.round(cx * 3), hr = 7 + Math.round(sy * 3);
      hammer(hc, hr, w.a);
      P(hc, hr, PAL.S);
    } else {
      armDown(8);
      if (w.hammer === 'hand') hammer(8, 10, 0.9);
    }
  }

  function drawBubble() {
    if (!bubble || worker.arms === 'up' && state === 'repair') return;
    const g = GLYPHS[bubble.g];
    const gw = g[0].length;
    const bw = gw + 4, bh = 9;
    const cx = Math.round(worker.x) + 5;
    const bx = cx - Math.floor(bw / 2);
    const by = Math.round(floorY - 16 + worker.jy) - bh - 4;
    rect(bx - 1, by - 1, bw + 2, bh + 2, PAL.K);
    rect(bx, by, bw, bh, '#ffffff');
    rect(cx, by + bh, 1, 1, '#ffffff');
    rect(cx, by + bh + 1, 1, 1, PAL.K);
    const col = bubble.g === '?' ? PAL.K : '#e5484d';
    g.forEach((row, r) => { for (let c = 0; c < row.length; c++) if (row[c] === 'X') rect(bx + 2 + c, by + 2 + r, 1, 1, col); });
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    if (CONFIG.showGround) rect(0, floorY, W, 1, groundColor);

    const sx = shake > 0 ? Math.round((Math.random() - 0.5) * 2 * shake) : 0;
    units.filter(u => u.mode === 'stacked').sort((a, b) => a.i - b.i).forEach(u => drawUnit(u, sx));
    units.filter(u => u.mode === 'ground' || u.mode === 'flying').forEach(u => drawUnit(u, 0));
    drawWorker();
    units.filter(u => u.mode === 'held').forEach(u => drawUnit(u, 0));

    for (const p of particles) {
      ctx.globalAlpha = p.fade ? Math.max(0, p.life / p.max) * 0.8 : 1;
      rect(Math.round(p.x), Math.round(p.y), p.size, p.size, p.color);
    }
    ctx.globalAlpha = 1;
    drawBubble();
  }

  // ---------- run ----------
  let running = false;
  function start() {
    builtForWidth = window.innerWidth;
    if (phone.matches) { running = false; return; }
    layout();
    buildScene();
    if (reducedMotion) {
      worker.x = serverX - 13;
      draw();
      return;
    }
    newRound();
    if (!running) {
      running = true;
      last = performance.now();
      requestAnimationFrame(frame);
    }
  }

  let last = 0;
  function frame(now) {
    if (!running) return;
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    update(dt);
    draw();
    requestAnimationFrame(frame);
  }

  let resizeTimer;
  window.addEventListener('resize', () => {
    if (window.innerWidth === builtForWidth) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(start, 200);
  });

  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    readGroundColor();
    if (reducedMotion && !phone.matches) draw();
  });

  start();
})();
