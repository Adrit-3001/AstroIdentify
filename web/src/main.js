/**
 * Entry point: one clock drives everything. `g` is seconds since the first rendered frame; the
 * comet intro reads `g` directly, and the flight reads `t = g - handoffAt` (the intro's length),
 * so the camera, comet, wordmark, trails and text reveal can never drift apart.
 *
 * Debug URL parameters (for tuning and testing; harmless in production):
 *   ?t=1.2      freeze the clock at 1.2 s since the page opened (intro included)
 *   ?nointro    skip the comet intro for this load (same as cometIntro.enabled = false)
 *   ?reduced    behave as if prefers-reduced-motion were set
 *   ?hud        show time / speed / phase in the corner
 */
import { createCamera } from './camera.js';
import { LANDING_CONFIG as config } from './config.js';
import { createReveal } from './content/reveal.js';
import { createIntroTimeline } from './intro/introTimeline.js';
import { createWordmark } from './intro/wordmark.js';
import { createRenderer } from './scene/renderer.js';
import { createTimeline } from './timeline.js';

const root = document.documentElement;
const params = new URLSearchParams(location.search);
const reducedQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
const frozenTime = params.has('t') ? Number(params.get('t')) : null;

function pickQuality() {
  const shortSide = Math.min(window.innerWidth, window.innerHeight);
  const { mobile, desktop } = config.quality;
  return shortSide < mobile.maxWidthPx ? { name: 'mobile', ...mobile } : { name: 'desktop', ...desktop };
}

function start() {
  const reduced = reducedQuery.matches || params.has('reduced');
  const introEnabled = config.cometIntro.enabled && !reduced && !params.has('nointro');
  const timeline = createTimeline(config, { leadIn: introEnabled });
  const intro = createIntroTimeline(config, timeline, { enabled: introEnabled });
  const camera = createCamera(config, timeline, { reducedMotion: reduced, intro });
  const canvas = document.getElementById('sky');
  const quietZone = document.querySelector('.quiet-zone');
  const introMark = document.querySelector('.intro-mark');
  const revealEls = [...document.querySelectorAll('[data-reveal]')].sort(
    (a, b) => Number(a.dataset.reveal) - Number(b.dataset.reveal),
  );
  const reveal = createReveal(revealEls, quietZone, timeline, config);
  const settledG = intro.handoffAt + timeline.settledTime;
  const state = { time: reduced ? settledG : 0, frames: 0, firstFrameAt: null };
  window.__landing = { config, timeline, intro, state };

  if (!introEnabled) introMark.hidden = true;

  let renderer;
  try {
    renderer = createRenderer(canvas, config, pickQuality(), { introEnabled });
  } catch (error) {
    // No WebGL2: keep the static page fully usable on the plain dark background.
    console.warn('[AstroIdentify] sky disabled:', error);
    root.dataset.layout =
      window.innerWidth / window.innerHeight < config.composition.narrowBelowAspectRatio ? 'narrow' : 'wide';
    introMark.hidden = true;
    reveal.finish();
    return;
  }
  window.__landing.renderer = renderer;
  window.__landing.camera = camera;
  const wordmark = introEnabled ? createWordmark(introMark) : null;
  window.__landing.wordmark = wordmark;
  // True once the intro wordmark is gone for good (faded out, skipped, or no intro).
  let markDone = !introEnabled;

  if (reduced) reveal.finish();
  // Keyboard users who tab in during the opening get the page immediately.
  document.querySelector('.hero').addEventListener('focusin', () => {
    markDone = true;
    introMark.hidden = true;
    reveal.finish();
  });

  function applyLayout() {
    const rect = canvas.getBoundingClientRect();
    const view = renderer.resize(rect.width, rect.height, window.devicePixelRatio);
    root.dataset.layout = view.layoutName;
    // The comet starts on the i's dot and loops around the word, so measure the wordmark as
    // laid out now (font size depends on the viewport).
    if (wordmark && !markDone) renderer.setIntroAnchor(wordmark.measure(rect.width, rect.height));
    const q = view.layout.quietZone;
    quietZone.style.setProperty('--qx', `${q.x * 100}%`);
    quietZone.style.setProperty('--qy', `${q.y * 100}%`);
    quietZone.style.setProperty('--qrx', `${q.radiusX * 100}%`);
    quietZone.style.setProperty('--qry', `${q.radiusY * 100}%`);
  }
  applyLayout();
  // Re-measure once web/system fonts are settled, in case the first layout used a fallback.
  document.fonts?.ready.then(() => {
    applyLayout();
    requestFrame();
  });

  const hud = params.has('hud') ? createHud() : null;
  const animate = frozenTime === null && (!reduced || config.reducedMotion.ambientDrift);
  let lastTimestamp = null;
  let pending = false;

  function draw() {
    const g = frozenTime ?? state.time;
    const t = g - intro.handoffAt;
    const cam = camera.frameAt(t);
    renderer.render(cam, {
      approachDistance: timeline.approachDistance,
      brightness: intro.skyBrightness(g),
      starReveal: intro.starReveal(g),
      deepSky: intro.deepSkyVisibility(g),
      comet: introEnabled
        ? { g, visibility: intro.cometVisibility(g), ignition: intro.ignitionAt(g), glow: intro.glowAt(g) }
        : null,
    });
    if (!markDone) {
      // The i's dot: untouched until detach, then it follows the comet path and hands over to
      // the comet's nucleus as that ignites.
      const detached = g >= intro.moments.detach;
      const position = detached ? renderer.cometScreenPosition(g, cam.turn) : null;
      wordmark.update(detached && position !== null, position, 1 - intro.ignitionAt(g));
      const opacity = intro.logoOpacity(g);
      introMark.style.opacity = String(opacity);
      if (opacity <= 0) {
        introMark.hidden = true;
        markDone = frozenTime === null;
      } else {
        introMark.hidden = false;
      }
    }
    reveal.update(t);
    if (hud) hud.textContent = hudText(timeline, intro, g);
  }

  function frame(timestamp) {
    pending = false;
    // Clamp long gaps (a stalled frame, a background tab) so the opening briefly slows
    // instead of jumping ahead. Normal frames at any refresh rate are far below this.
    const dt = lastTimestamp === null ? 0 : Math.min(0.1, (timestamp - lastTimestamp) / 1000);
    lastTimestamp = timestamp;
    if (state.frames === 0) state.firstFrameAt = performance.now();
    state.time += dt;
    camera.stepSpring(dt);
    draw();
    state.frames++;
    if (animate) requestFrame();
  }

  function requestFrame() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(frame);
  }

  new ResizeObserver(() => {
    applyLayout();
    requestFrame();
  }).observe(canvas);

  if (camera.parallaxEnabled && window.matchMedia('(pointer: fine)').matches) {
    window.addEventListener('pointermove', (e) => {
      camera.setPointer((e.clientX / window.innerWidth) * 2 - 1, (e.clientY / window.innerHeight) * 2 - 1);
    });
    document.addEventListener('pointerleave', () => camera.setPointer(null, null));
  }

  reducedQuery.addEventListener('change', () => window.location.reload());
  canvas.addEventListener('webglcontextlost', (e) => e.preventDefault());
  canvas.addEventListener('webglcontextrestored', () => window.location.reload());

  requestFrame();
}

function createHud() {
  const el = document.createElement('pre');
  el.style.cssText =
    'position:fixed;right:8px;bottom:8px;margin:0;padding:6px 8px;font:11px/1.4 ui-monospace,monospace;' +
    'color:#9fb0d8;background:rgba(0,0,0,.6);border-radius:6px;z-index:10;pointer-events:none';
  document.body.append(el);
  return el;
}

function hudText(timeline, intro, g) {
  const t = g - intro.handoffAt;
  const { accelerationEnd, slowdownStart, flightEnd } = timeline.phases;
  const phase =
    t < 0 ? 'comet intro' : t < accelerationEnd ? 'accelerate' : t < slowdownStart ? 'peak' : t < flightEnd ? 'slowdown' : 'ambient';
  return (
    `g ${g.toFixed(3)} s  (flight t ${t.toFixed(3)} s)\n` +
    `speed ${timeline.speedAt(t).toFixed(2)} u/s\nturn ${intro.turnProgress(g).toFixed(3)}\nphase ${phase}`
  );
}

start();
