"use strict";
// Effects: the starfield behind everything, the warp at liftoff, embers off a burning ring, haptics and
// the little text "decode". Purely visual; nothing here talks to the API.
window.FX = (() => {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const cvs = document.getElementById("sky");
  const ctx = cvs.getContext("2d");
  const ACCENT = [226, 58, 41];

  // How fast the ship is moving, in depth units a second, for each state of the console.
  const SPEEDS = { idle: 0.018, arming: 0.05, countdown: 0.035, burning: 0.075, off: 0.006 };

  let W = 0, H = 0, dpr = 1;
  let stars = [];
  let embers = [];
  let speed = SPEEDS.idle, target = SPEEDS.idle;
  let boost = 0;          // extra speed from a liftoff, decaying back to cruise
  let pull = 0;           // 0..1, how hard the field strains inward while arming
  let alpha = 1, alphaTarget = 1;
  let vx = 0.5, vy = 0.38; // vanishing point, as a fraction of the viewport
  let emitter = null;      // where embers come from: {x, y, dx, dy} in CSS px
  let emberDebt = 0;
  let raf = 0, last = 0;

  function resize() {
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = window.innerWidth; H = window.innerHeight;
    cvs.width = Math.round(W * dpr); cvs.height = Math.round(H * dpr);
    const want = Math.round(Math.min(280, Math.max(90, (W * H) / 3200)));
    while (stars.length < want) stars.push(star(true));
    stars.length = want;
    if (reduced.matches) draw(0);
  }

  function star(anyDepth) {
    return {
      x: (Math.random() * 2 - 1) * 1.2,
      y: (Math.random() * 2 - 1) * 1.2,
      z: anyDepth ? Math.random() * 0.95 + 0.05 : 1,
      tw: Math.random() * Math.PI * 2,
      big: Math.random() < 0.06,
    };
  }

  const scale = () => Math.max(W, H) * 0.55;
  const proj = (s, z) => [vx * W + (s.x / z) * scale(), vy * H + (s.y / z) * scale()];

  function draw(dt) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const v = speed + boost;
    const streak = Math.min(0.5, v * 0.16);
    const t = performance.now() / 1000;
    ctx.lineCap = "round";
    for (const s of stars) {
      s.z -= v * dt;
      // While arming, stars creep back toward the vanishing point: the engines straining against the clamps.
      if (pull) { const k = 1 - pull * 0.22 * dt; s.x *= k; s.y *= k; }
      const [x, y] = proj(s, s.z);
      if (s.z <= 0.03 || x < -40 || x > W + 40 || y < -40 || y > H + 40) { Object.assign(s, star(false)); continue; }
      const near = 1 - s.z;
      const tw = reduced.matches ? 1 : 0.75 + 0.25 * Math.sin(t * (1.3 + s.tw) + s.tw * 7);
      const a = Math.min(1, near * near * 1.4 + 0.22) * tw * alpha;
      const r = (s.big ? 1.25 : 0.7) * (0.35 + near * 1.1);
      if (streak > 0.025) {
        const [px, py] = proj(s, Math.min(1.2, s.z + streak * s.z));
        ctx.strokeStyle = `rgba(255,255,255,${a})`;
        ctx.lineWidth = r * 1.4;
        ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(x, y); ctx.stroke();
      } else {
        ctx.fillStyle = `rgba(255,255,255,${a})`;
        ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fill();
      }
    }

    // Embers: sparks thrown off the head of a burning ring, like a lit fuse.
    if (emitter && !reduced.matches && dt) {
      emberDebt += dt * 26;
      while (emberDebt >= 1) {
        emberDebt -= 1;
        const spread = (Math.random() - 0.5) * 1.6;
        const sp = 18 + Math.random() * 46;
        const dx = emitter.dx * Math.cos(spread) - emitter.dy * Math.sin(spread);
        const dy = emitter.dx * Math.sin(spread) + emitter.dy * Math.cos(spread);
        embers.push({ x: emitter.x, y: emitter.y, vx: dx * sp, vy: dy * sp - 8, life: 0, max: 0.6 + Math.random() * 0.9, r: 0.6 + Math.random() * 1.3 });
      }
    }
    if (embers.length) {
      ctx.globalCompositeOperation = "lighter";
      embers = embers.filter((e) => (e.life += dt) < e.max);
      for (const e of embers) {
        e.vy -= 14 * dt; // heat rises
        e.vx *= 1 - 1.2 * dt; e.vy *= 1 - 1.2 * dt;
        e.x += e.vx * dt; e.y += e.vy * dt;
        const k = 1 - e.life / e.max;
        const hot = Math.max(0, k - 0.6) / 0.4; // starts nearly white, cools to the accent
        const c = ACCENT.map((ch) => Math.round(ch + (255 - ch) * hot * 0.8));
        ctx.fillStyle = `rgba(${c[0]},${c[1]},${c[2]},${k * 0.9})`;
        ctx.beginPath(); ctx.arc(e.x, e.y, e.r * (0.5 + k), 0, 6.2832); ctx.fill();
      }
      ctx.globalCompositeOperation = "source-over";
    }
  }

  function frame(now) {
    const dt = Math.min(0.05, (now - (last || now)) / 1000);
    last = now;
    speed += (target - speed) * Math.min(1, dt * 1.6);
    boost *= Math.exp(-dt * 1.25);
    if (boost < 0.002) boost = 0;
    alpha += (alphaTarget - alpha) * Math.min(1, dt * 3);
    draw(dt);
    raf = requestAnimationFrame(frame);
  }

  function start() {
    cancelAnimationFrame(raf);
    last = 0;
    if (reduced.matches || document.hidden) { draw(0); return; }
    raf = requestAnimationFrame(frame);
  }

  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", () => (document.hidden ? cancelAnimationFrame(raf) : start()));
  reduced.addEventListener("change", start);
  resize();
  start();

  /* ---- haptics: the Android app's own clicks, or the Vibration API where a browser has one */

  const NATIVE = window.IgnitionNative && window.IgnitionNative.haptic ? window.IgnitionNative : null;
  const PATTERNS = { tick: 4, confirm: [30, 50, 30, 50, 220], heavy: 40, reject: [60, 60, 60] };
  function haptic(kind) {
    try {
      if (NATIVE) NATIVE.haptic(kind);
      else if (navigator.vibrate) navigator.vibrate(PATTERNS[kind] || 20);
    } catch { /* no haptics here */ }
  }

  /* ---- text decode: characters spin through noise and lock in left to right */

  const NOISE = "0123456789ABCDEFGHJKLMNPRSTUVXYZ#%/<>=+";
  const decoding = new WeakMap();
  function decode(el, text, ms = 420) {
    const prev = decoding.get(el);
    if (prev) cancelAnimationFrame(prev);
    if (reduced.matches) { el.textContent = text; return; }
    const t0 = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - t0) / ms);
      const fixed = Math.floor(p * text.length);
      let out = text.slice(0, fixed);
      for (let i = fixed; i < text.length; i++) out += text[i] === " " ? " " : NOISE[(Math.random() * NOISE.length) | 0];
      el.textContent = out;
      if (p < 1) decoding.set(el, requestAnimationFrame(step));
      else { el.textContent = text; decoding.delete(el); }
    };
    decoding.set(el, requestAnimationFrame(step));
  }

  /* ---- a number that counts up to its value */

  function countUp(el, to, fmt, ms = 900) {
    if (reduced.matches || !to) { el.textContent = fmt(to); return; }
    const t0 = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - t0) / ms);
      el.textContent = fmt(to * (1 - Math.pow(1 - p, 4)));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /** Restart a CSS animation class on an element. */
  function replay(el, cls) {
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
  }

  return {
    /** idle | arming | countdown | burning | off */
    mode(name) { target = SPEEDS[name] ?? SPEEDS.idle; if (name !== "arming") pull = 0; },
    /** Fade the field down on the quieter pages. */
    dim(v) { alphaTarget = v; },
    /** Put the vanishing point on a point of the screen (the dial). */
    focus(x, y) { if (W && H) { vx = x / W; vy = y / H; } },
    /** Embers from (x, y), thrown along (dx, dy); null to stop. */
    emitter(e) { emitter = e; },
    /** 3, 2, 1: strain a little harder each count. */
    spool(n) { pull = (4 - n) / 3; target = SPEEDS.arming * (1 + (3 - n) * 0.4); haptic("heavy"); },
    liftoff() {
      pull = 0;
      if (!reduced.matches) boost = 2.6;
      const f = document.getElementById("flash");
      if (f) replay(f, "go");
      haptic("confirm");
    },
    cutoff() { target = SPEEDS.off; boost = 0; emitter = null; },
    haptic, decode, countUp, replay,
    get reduced() { return reduced.matches; },
  };
})();
