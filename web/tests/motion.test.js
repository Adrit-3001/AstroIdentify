/**
 * Motion tests for the landing page, run in a browser (no build tools needed):
 *   python3 web/serve.py   then open http://127.0.0.1:8765/tests/
 * The page title becomes "PASS" or "FAIL".
 *
 * The first group checks the flight on its own (comet intro off = the original opening); the
 * "intro:" group checks the comet intro and its handoffs into that same flight.
 */
import { createCamera } from '../src/camera.js';
import { LANDING_CONFIG } from '../src/config.js';
import { createIntroGeometry } from '../src/intro/geometry.js';
import { createIntroTimeline } from '../src/intro/introTimeline.js';
import { apply, IDENTITY } from '../src/math3.js';
import { createView } from '../src/scene/composition.js';
import { createTimeline, EASINGS } from '../src/timeline.js';

const results = [];
function test(name, fn) {
  try {
    const note = fn();
    results.push({ name, ok: true, note });
  } catch (error) {
    results.push({ name, ok: false, note: error.message });
  }
}
function assert(cond, message) {
  if (!cond) throw new Error(message);
}

const config = structuredClone(LANDING_CONFIG);
const timeline = createTimeline(config);
const { accelerationEnd, slowdownStart, flightEnd } = timeline.phases;
const view = createView(config, 1440, 900, 1);
const maxTrail = Math.min(
  config.trails.maxLengthPx,
  config.trails.maxLengthShortSideFraction * view.shortSide,
);

/** The shader's soft trail cap. */
function softCap(len) {
  const knee = 0.75 * maxTrail;
  if (len <= knee) return len;
  const span = maxTrail - knee;
  return knee + span * (1 - Math.exp(-(len - knee) / span));
}

/** Same maths as the star vertex shader, for one field star (lateral x, y; settled depth z). */
const glslMod = (a, b) => a - b * Math.floor(a / b);
function fieldStarTrail(star, cam, rot = IDENTITY, rotPrev = IDENTITY) {
  const s = config.stars;
  const [sx, sy] = view.slope;
  const camNow = [sx * cam.field + cam.lateral[0], sy * cam.field + cam.lateral[1], cam.field];
  const camPrev = [
    sx * cam.fieldPrev + cam.lateralPrev[0],
    sy * cam.fieldPrev + cam.lateralPrev[1],
    cam.fieldPrev,
  ];
  const z = s.nearDepth + glslMod(star.z - camNow[2] - s.nearDepth, s.farDepth - s.nearDepth);
  const wrapX = (v) => v; // test stars are chosen well inside the wrap box
  const rel = apply(rot, [wrapX(star.x - camNow[0]), star.y - camNow[1], z]);
  const relPrev = apply(rotPrev, [
    star.x - camNow[0] + camNow[0] - camPrev[0],
    star.y - camNow[1] + camNow[1] - camPrev[1],
    z + camNow[2] - camPrev[2],
  ]);
  const head = [view.center[0] + (view.focal * rel[0]) / rel[2], view.center[1] + (view.focal * rel[1]) / rel[2]];
  const tail = [
    view.center[0] + (view.focal * relPrev[0]) / relPrev[2],
    view.center[1] + (view.focal * relPrev[1]) / relPrev[2],
  ];
  return { head, length: softCap(Math.hypot(head[0] - tail[0], head[1] - tail[1])) };
}

const camera = createCamera(config, timeline);

/** The field star that appears at screen position (x, y view fractions) and `depth` at time t. */
function starSeenAt(t, x, y, depth) {
  const s = config.stars;
  const cam = camera.frameAt(t);
  const [lx, ly] = view.lateralAt(view.toPx({ x, y }), depth);
  const camX = view.slope[0] * cam.field + cam.lateral[0];
  const camY = view.slope[1] * cam.field + cam.lateral[1];
  const z = s.nearDepth + glslMod(depth + cam.field - s.nearDepth, s.farDepth - s.nearDepth);
  return { x: lx + camX, y: ly + camY, z };
}

const peakTime = accelerationEnd + config.flight.peakHoldSeconds / 2;
const foe = config.composition.wide.focusOfExpansion;

test('opens already moving and visible', () => {
  assert(timeline.speedAt(0) > 0, 'speed at the first frame must be > 0');
  assert(timeline.openingBrightnessAt(0) >= 0.5, 'first frame must not be blank');
  return `speed(0) = ${timeline.speedAt(0)} u/s, brightness(0) = ${timeline.openingBrightnessAt(0)}`;
});

test('easings start at 0 and end at 1', () => {
  for (const [name, make] of Object.entries(EASINGS)) {
    const e = make({ tailExponent: 2.6 });
    assert(Math.abs(e(0)) < 1e-12 && Math.abs(e(1) - 1) < 1e-12, `${name} endpoints`);
  }
});

test('speed curve has no jumps', () => {
  let worst = 0;
  const h = 1e-4;
  for (let t = -0.2; t < flightEnd + 1; t += h) {
    worst = Math.max(worst, Math.abs(timeline.speedAt(t + h) - timeline.speedAt(t)));
  }
  assert(worst < 0.02, `largest speed change in 0.1 ms was ${worst}`);
  return `largest change per 0.1 ms: ${worst.toFixed(5)} u/s`;
});

test('acceleration is continuous at every phase join (no jolt, no abrupt stop)', () => {
  const accel = (t) => (timeline.speedAt(t + 1e-5) - timeline.speedAt(t - 1e-5)) / 2e-5;
  const notes = [];
  for (const [name, b] of Object.entries({ start: 0, accelerationEnd, slowdownStart, flightEnd })) {
    const before = accel(b - 2e-3);
    const after = accel(b + 2e-3);
    assert(Math.abs(before - after) < 1.5, `${name}: ${before.toFixed(3)} vs ${after.toFixed(3)} u/s²`);
    notes.push(`${name} ${before.toFixed(2)}→${after.toFixed(2)}`);
  }
  return notes.join(', ');
});

test('distance is the exact integral of speed and always increases', () => {
  let numeric = 0;
  let previous = timeline.distanceAt(0);
  const h = 1e-5;
  for (let t = 0; t < flightEnd + 2; t += h) {
    numeric += ((timeline.speedAt(t) + timeline.speedAt(t + h)) / 2) * h;
    const d = timeline.distanceAt(t + h);
    assert(d >= previous, `distance decreased at t=${t}`);
    previous = d;
  }
  const exact = timeline.distanceAt(flightEnd + 2);
  assert(Math.abs(numeric - exact) < 1e-4, `integral ${numeric} vs ${exact}`);
  return `flight distance ${timeline.flightDistance.toFixed(3)} u`;
});

test('deep-sky approach glides to an exact stop', () => {
  const v = (t) => (timeline.approachAt(t + 1e-4) - timeline.approachAt(t - 1e-4)) / 2e-4;
  assert(v(flightEnd - 0.05) < 0.02, `approach speed just before the end: ${v(flightEnd - 0.05)}`);
  assert(Math.abs(v(flightEnd + 0.5)) < 1e-9, 'approach must be stopped after the flight');
  assert(Math.abs(timeline.approachAt(flightEnd + 60) - timeline.approachDistance) < 1e-9, 'settled');
});

test('frame-rate independent: 60 Hz, 120 Hz and jittery 144 Hz agree', () => {
  const run = (frames) => {
    const cam = createCamera(config, timeline);
    cam.setPointer(0.6, -0.4);
    let t = 0;
    for (const dt of frames) {
      t += dt;
      cam.stepSpring(dt);
    }
    return { t, state: cam.frameAt(t) };
  };
  const total = 5;
  const hz60 = Array(300).fill(1 / 60);
  const hz120 = Array(600).fill(1 / 120);
  const jitter = [];
  let sum = 0;
  for (let i = 0; sum < total - 1e-9; i++) {
    const dt = Math.min(total - sum, (1 / 144) * (1 + 0.3 * Math.sin(i * 1.7)));
    jitter.push(dt);
    sum += dt;
  }
  const a = run(hz60);
  const b = run(hz120);
  const c = run(jitter);
  const diff = (x, y) =>
    Math.max(
      Math.abs(x.state.field - y.state.field),
      Math.abs(x.state.approach - y.state.approach),
      Math.abs(x.state.lateral[0] - y.state.lateral[0]),
      Math.abs(x.state.lateral[1] - y.state.lateral[1]),
    );
  assert(diff(a, b) < 1e-6, `60 vs 120 Hz differ by ${diff(a, b)}`);
  // The parallax spring steps at a fixed 1/240 s; jittery frames land within one step of it.
  assert(diff(a, c) < 2e-3, `60 vs jittery 144 Hz differ by ${diff(a, c)}`);
  const star = starSeenAt(total, 0.15, 0.3, 8);
  const trail60 = fieldStarTrail(star, a.state).length;
  const trail120 = fieldStarTrail(star, b.state).length;
  assert(Math.abs(trail60 - trail120) < 1e-3, 'trail length must not depend on frame rate');
  return `max difference 60/120 Hz ${diff(a, b).toExponential(1)} u; jittery ${diff(a, c).toExponential(1)} u`;
});

test('first frame: short readable streaks; peak: long but capped; settled: points', () => {
  // A close star (depth 8 u) seen at the same screen spot, away from the heading, each moment.
  const at = (t) => fieldStarTrail(starSeenAt(t, 0.15, 0.3, 8), camera.frameAt(t)).length;
  const first = at(0);
  const peak = at(peakTime);
  const settled = at(flightEnd + 2);
  assert(first > 4 && first < 90, `first-frame trail ${first.toFixed(1)} px should be short but visible`);
  assert(peak > first * 2.5, `peak trail ${peak.toFixed(1)} px should be much longer than ${first.toFixed(1)}`);
  assert(peak <= maxTrail + 1e-6, 'peak trail must respect the cap');
  // Settled stars still drift very slowly; a sub-pixel smear renders as a round point.
  assert(settled < 1, `settled trail ${settled.toFixed(2)} px should be a point`);
  return `near star: first ${first.toFixed(1)} px, peak ${peak.toFixed(1)} px (cap ${maxTrail}), settled ${settled.toFixed(2)} px`;
});

test('peak keeps depth: distant stars near the heading stay almost points', () => {
  const t = peakTime;
  const far = fieldStarTrail(starSeenAt(t, foe.x + 0.03, foe.y + 0.02, 110), camera.frameAt(t)).length;
  const near = fieldStarTrail(starSeenAt(t, 0.15, 0.3, 8), camera.frameAt(t)).length;
  assert(far < 6, `distant star near the heading streaks ${far.toFixed(2)} px at peak`);
  assert(near > far * 10, 'near stars must streak far more than distant ones');
  return `at peak: distant ${far.toFixed(2)} px vs near ${near.toFixed(1)} px`;
});

test('final second: on-screen motion fades out with no visible break', () => {
  // Screen speed of a star that is 8 u away during the final second, sampled at 120 Hz.
  const nearStar = starSeenAt(flightEnd - 1, 0.15, 0.3, 8);
  const step = 1 / 120;
  const speeds = [];
  for (let t = slowdownStart; t <= flightEnd + 1.5; t += step) {
    const a = fieldStarTrail(nearStar, camera.frameAt(t)).head;
    const b = fieldStarTrail(nearStar, camera.frameAt(t + step)).head;
    speeds.push({ t, v: Math.hypot(b[0] - a[0], b[1] - a[1]) / step });
  }
  const decel = speeds.slice(1).map((s, i) => ({ t: s.t, a: (speeds[i].v - s.v) / step }));
  const maxDecel = Math.max(...decel.map((d) => d.a));
  const nearEnd = decel.filter((d) => Math.abs(d.t - flightEnd) < 0.1);
  const endDecel = Math.max(...nearEnd.map((d) => Math.abs(d.a)));
  assert(endDecel < maxDecel * 0.03, `deceleration at the end ${endDecel} vs max ${maxDecel}`);
  // No kink anywhere: acceleration never steps between consecutive 120 Hz samples.
  let worstStep = 0;
  for (let i = 1; i < decel.length; i++) worstStep = Math.max(worstStep, Math.abs(decel[i].a - decel[i - 1].a));
  assert(worstStep < maxDecel * 0.03, `acceleration stepped by ${worstStep} px/s² between frames`);
  const lastSecond = speeds.filter((s) => s.t >= flightEnd - 1 && s.t <= flightEnd);
  return (
    `near-star screen speed over the final second: ${lastSecond[0].v.toFixed(1)} → ` +
    `${lastSecond[lastSecond.length - 1].v.toFixed(2)} px/s; ` +
    `deceleration at the end is ${((endDecel / maxDecel) * 100).toFixed(2)}% of its maximum`
  );
});

test('text reveal is scheduled from the slowdown and stays in sync when it changes', () => {
  const revealStart = slowdownStart + config.flight.slowdownSeconds * config.textReveal.startAtSlowdownProgress;
  assert(timeline.revealAt(revealStart - 0.01, 0) === 0, 'headline hidden before its start');
  assert(timeline.revealAt(revealStart + 0.2, 0) > 0, 'headline appearing after its start');
  assert(revealStart > slowdownStart && revealStart < flightEnd, 'reveal begins during the slowdown');
  assert(timeline.revealAt(timeline.settledTime, 2) === 1, 'all text visible once settled');
  const slower = createTimeline({ ...config, flight: { ...config.flight, slowdownSeconds: 6 } });
  const slowerStart = slower.phases.slowdownStart + 6 * config.textReveal.startAtSlowdownProgress;
  assert(slower.revealAt(slowerStart - 0.01, 0) === 0 && slower.revealAt(slowerStart + 0.2, 0) > 0,
    'reveal must follow a longer slowdown');
  return `headline starts at ${revealStart.toFixed(2)} s (flight ends ${flightEnd.toFixed(2)} s)`;
});


// ---------------------------------------------------------------------------------------------
// Comet intro
// ---------------------------------------------------------------------------------------------
const iFlight = createTimeline(config, { leadIn: true });
const intro = createIntroTimeline(config, iFlight, { enabled: true });
const iCamera = createCamera(config, iFlight, { intro });
const geo = createIntroGeometry(config, view);
const H = intro.handoffAt;
const { detach, departure, turnStart, leadInStart } = intro.moments;
const ci = config.cometIntro;

const project = (rot, p) => {
  const q = apply(rot, p);
  return [view.center[0] + (view.focal * q[0]) / q[2], view.center[1] + (view.focal * q[1]) / q[2]];
};
const rotAt = (g) => geo.rotationAt(intro.turnProgress(g));
const cometScreen = (g) => project(rotAt(g), geo.positionAt(g));
const cometScreenSpeed = (g, h = 1 / 240) => {
  const a = cometScreen(g - h / 2);
  const b = cometScreen(g + h / 2);
  return Math.hypot(b[0] - a[0], b[1] - a[1]) / h;
};
const cometSpeed3d = (g, h = 1 / 240) => {
  const a = geo.positionAt(g - h / 2);
  const b = geo.positionAt(g + h / 2);
  return Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) / h;
};
/** Where the flight heading (focus of expansion) is on screen at time g. */
const foeScreen = (g) => project(rotAt(g), [view.slope[0], view.slope[1], 1]);

test('intro: opens on black with the untouched wordmark; camera still during the loop', () => {
  assert(intro.logoOpacity(0) === 1, 'wordmark fully visible on the first frame');
  assert(intro.starReveal(0) === 0, 'no stars on the first frame (black)');
  assert(intro.ignitionAt(0) === 0 && detach > 0.3, 'the dot is still on the i at first');
  assert(leadInStart >= departure, 'camera must not move forward before the comet departs');
  for (let g = 0; g < Math.min(leadInStart, turnStart); g += 0.01) {
    assert(iFlight.speedAt(g - H) === 0 && intro.turnProgress(g) === 0, `camera moved at g=${g}`);
  }
  return `dot detaches at ${detach} s; camera still until ${Math.min(leadInStart, turnStart).toFixed(2)} s`;
});

test('intro: the comet starts exactly on the i’s dot, from rest, and ignites while moving', () => {
  const tittlePx = [geo.anchor.tittle[0] * view.width, geo.anchor.tittle[1] * view.height];
  const start = cometScreen(detach);
  const offset = Math.hypot(start[0] - tittlePx[0], start[1] - tittlePx[1]);
  assert(offset < 0.5, `comet starts ${offset.toFixed(2)} px from the dot`);
  assert(cometScreenSpeed(detach + 0.002, 0.002) < 15, 'the dot leaves from rest');
  assert(cometScreenSpeed(detach + 0.2) > 150, 'and is clearly moving soon after');
  const peak = ci.glow.peakAtSeconds;
  assert(intro.glowAt(peak) > 0.9 * ci.glow.intensity && cometScreenSpeed(peak) > 100, 'shines while moving');
  const full = detach + ci.ignition.delaySeconds + ci.ignition.seconds;
  assert(intro.ignitionAt(detach) === 0 && intro.ignitionAt(full) === 1, 'ignites from dot to comet');
  return `starts ${offset.toFixed(2)} px from the dot; ${cometScreenSpeed(detach + 0.2).toFixed(0)} px/s after 0.2 s; full comet by ${full.toFixed(2)} s`;
});

test('intro: the comet loops once around the word without crossing it', () => {
  const { cx, cy, hw, hh } = geo.anchor.box;
  const c = [cx * view.width, cy * view.height];
  const half = [hw * view.width, hh * view.height];
  const loopStart = ci.path.loop[0].at;
  let winding = 0;
  let prevAngle = null;
  let closest = Infinity;
  for (let g = loopStart; g <= departure; g += 1 / 240) {
    const p = cometScreen(g);
    const a = Math.atan2(p[1] - c[1], p[0] - c[0]);
    if (prevAngle !== null) winding += Math.atan2(Math.sin(a - prevAngle), Math.cos(a - prevAngle));
    prevAngle = a;
    // Clearance from the word's ink box, px (negative = inside it).
    const dx = Math.abs(p[0] - c[0]) - half[0];
    const dy = Math.abs(p[1] - c[1]) - half[1];
    closest = Math.min(closest, Math.max(dx, dy));
  }
  const degrees = (Math.abs(winding) * 180) / Math.PI;
  assert(degrees > 300, `loop winds only ${degrees.toFixed(0)}° around the word`);
  assert(closest > 0.4 * half[1], `loop comes within ${closest.toFixed(1)} px of the letters`);
  return `winds ${degrees.toFixed(0)}° around the word, never closer than ${closest.toFixed(0)} px to the letters`;
});

test('intro: comet visibly accelerates away before the camera turns', () => {
  let loopSpeed = 0;
  let samples = 0;
  for (let g = ci.path.loop[0].at; g <= departure; g += 0.02, samples++) loopSpeed += cometSpeed3d(g);
  loopSpeed /= samples;
  const atTurn = cometSpeed3d(turnStart);
  assert(atTurn > 1.5 * loopSpeed, `comet should accelerate before the turn (${loopSpeed.toFixed(1)} → ${atTurn.toFixed(1)} u/s)`);
  assert(intro.turnProgress(turnStart) === 0, 'camera has not turned yet when the comet is speeding away');
  assert(intro.turnProgress(turnStart + 0.1) < 0.06, 'turn starts gently');
  return `travel speed ${loopSpeed.toFixed(1)} u/s around the loop → ${atTurn.toFixed(1)} u/s when the camera starts turning (${turnStart.toFixed(2)} s)`;
});

test('intro: handoff into the flight is one continuous launch (no pause, no second launch)', () => {
  const accel = (t) => (iFlight.speedAt(t + 1e-5) - iFlight.speedAt(t - 1e-5)) / 2e-5;
  const atHandoff = [accel(-2e-3), accel(2e-3)];
  assert(Math.abs(atHandoff[0] - atHandoff[1]) < 1.5, `acceleration jumps at the handoff: ${atHandoff}`);
  assert(Math.abs(iFlight.speedAt(0) - config.flight.openingSpeed) < 1e-9, 'handoff at the flight opening speed');
  // Acceleration rises to one maximum then falls: a single launch.
  let rising = true;
  let turns = 0;
  let prev = accel(leadInStart - H + 0.01);
  for (let t = leadInStart - H + 0.02; t < iFlight.phases.accelerationEnd - 0.01; t += 0.01) {
    const a = accel(t);
    if (rising && a < prev - 1e-6) [rising, turns] = [false, turns + 1];
    else if (!rising && a > prev + 1e-6) turns++;
    prev = a;
  }
  assert(turns === 1, `acceleration should have a single peak, changed direction ${turns} times`);
  return `acceleration across the handoff ${atHandoff[0].toFixed(2)} → ${atHandoff[1].toFixed(2)} u/s²`;
});

/** A set of field stars spread over the screen, seen at depth ~60 u when the turn starts. */
function sampleStars() {
  const stars = [];
  const cam = iCamera.frameAt(turnStart - H);
  const toWorld = geo.rotationAt(intro.turnProgress(turnStart)).slice();
  // Inverse of a rotation is its transpose.
  const inv = [toWorld[0], toWorld[3], toWorld[6], toWorld[1], toWorld[4], toWorld[7], toWorld[2], toWorld[5], toWorld[8]];
  for (let i = 0; i < 6; i++) {
    for (let j = 0; j < 4; j++) {
      const x = 0.1 + i * 0.16;
      const y = 0.12 + j * 0.25;
      const depth = 45 + ((i * 7 + j * 13) % 5) * 8;
      const eye = [((x * view.width - view.center[0]) / view.focal) * depth, ((y * view.height - view.center[1]) / view.focal) * depth, depth];
      const w = apply(inv, eye);
      const camX = view.slope[0] * cam.field + cam.lateral[0];
      const camY = view.slope[1] * cam.field + cam.lateral[1];
      stars.push({ x: w[0] + camX, y: w[1] + camY, z: w[2] + cam.field });
    }
  }
  return stars;
}

function motionField(g, stars, h = 1 / 120) {
  const cam = iCamera.frameAt(g - H);
  const camPrev = iCamera.frameAt(g - h - H);
  const foePx = foeScreen(g);
  return stars.map((star) => {
    const a = fieldStarTrail(star, camPrev, rotAt(g - h)).head;
    const b = fieldStarTrail(star, cam, rotAt(g)).head;
    const v = [b[0] - a[0], b[1] - a[1]];
    const r = [b[0] - foePx[0], b[1] - foePx[1]];
    const vl = Math.hypot(...v);
    const rl = Math.hypot(...r);
    return { v, vl, radial: vl > 1e-6 && rl > 1 ? (v[0] * r[0] + v[1] * r[1]) / (vl * rl) : 1, rl };
  });
}

test('intro: stars sweep sideways during the turn, then flow radially, with no cut', () => {
  const stars = sampleStars();
  const stats = (g) => {
    const f = motionField(g, stars).filter((m) => m.rl > 60 && m.vl > 1e-4);
    const ux = f.reduce((s, m) => s + m.v[0] / m.vl, 0) / f.length;
    const uy = f.reduce((s, m) => s + m.v[1] / m.vl, 0) / f.length;
    return { sweep: Math.hypot(ux, uy), radial: f.reduce((s, m) => s + m.radial, 0) / f.length };
  };
  const early = stats(turnStart + 0.3);
  const late = stats(H + 0.6);
  assert(early.sweep > 0.85, `early in the turn stars should move together sideways (alignment ${early.sweep.toFixed(2)})`);
  assert(late.radial > 0.97, `after the handoff stars should stream outward (radial ${late.radial.toFixed(2)})`);
  // No cut: each star's motion direction changes smoothly between 120 Hz frames.
  let worst = 0;
  let worstAt = 0;
  let prev = motionField(turnStart, stars);
  for (let g = turnStart + 1 / 120; g < H + 0.8; g += 1 / 120) {
    const cur = motionField(g, stars);
    cur.forEach((m, i) => {
      const p = prev[i];
      if (m.vl < 0.05 || p.vl < 0.05 || m.rl < 60) return;
      const cos = (m.v[0] * p.v[0] + m.v[1] * p.v[1]) / (m.vl * p.vl);
      const deg = (Math.acos(Math.min(1, Math.max(-1, cos))) * 180) / Math.PI;
      if (deg > worst) [worst, worstAt] = [deg, g];
    });
    prev = cur;
  }
  assert(worst < 6, `a star's direction jumped ${worst.toFixed(1)}° between frames at g=${worstAt.toFixed(3)} s`);
  return (
    `sideways alignment at turn+0.3 s: ${early.sweep.toFixed(2)} (radial ${early.radial.toFixed(2)}); ` +
    `radial at handoff+0.6 s: ${late.radial.toFixed(2)}; largest per-frame direction change ${worst.toFixed(1)}°`
  );
});

test('intro: trail lengths grow smoothly through the handoff (no jump)', () => {
  const stars = sampleStars();
  const exposure = config.trails.exposureSeconds;
  const longest = (g) => {
    const cam = iCamera.frameAt(g - H);
    return Math.max(...stars.map((s) => fieldStarTrail(s, cam, rotAt(g), rotAt(g - exposure)).length));
  };
  // A jump shows up as a spike in the frame-to-frame change (second difference); steady
  // growth of a fast-approaching star does not.
  const lengths = [];
  for (let g = turnStart; g < H + 0.8; g += 1 / 120) lengths.push(longest(g));
  let worstJerk = 0;
  let worstStep = 0;
  for (let i = 1; i < lengths.length - 1; i++) {
    worstJerk = Math.max(worstJerk, Math.abs(lengths[i + 1] - 2 * lengths[i] + lengths[i - 1]));
    worstStep = Math.max(worstStep, Math.abs(lengths[i + 1] - lengths[i]));
  }
  const at = lengths.findIndex((_, i) => i > 0 && i < lengths.length - 1 && Math.abs(lengths[i + 1] - 2 * lengths[i] + lengths[i - 1]) === worstJerk);
  assert(worstJerk < 1.5, `trail length kinked by ${worstJerk.toFixed(2)} px between frames at g=${(turnStart + at / 120).toFixed(3)} (length ${lengths[at].toFixed(1)}, cap ${maxTrail})`);
  return (
    `longest trail: ${longest(turnStart + 0.3).toFixed(1)} px mid-turn, ${longest(H).toFixed(1)} px at handoff, ` +
    `${longest(H + 0.6).toFixed(1)} px at +0.6 s; growth ≤ ${worstStep.toFixed(1)} px/frame, change in growth ≤ ${worstJerk.toFixed(2)} px`
  );
});

test('intro: wordmark is gone before the hero heading appears', () => {
  const headingStart = H + iFlight.phases.slowdownStart + config.flight.slowdownSeconds * config.textReveal.startAtSlowdownProgress;
  const goneAt = ci.logo.fadeStartSeconds + ci.logo.fadeSeconds;
  assert(intro.logoOpacity(goneAt) === 0 && goneAt < headingStart, 'wordmark must be gone first');
  assert(intro.logoOpacity(goneAt - ci.logo.fadeSeconds / 2) > 0 && goneAt > turnStart, 'wordmark fades during the turn');
  return `wordmark gone at ${goneAt.toFixed(2)} s; hero heading starts at ${headingStart.toFixed(2)} s`;
});

test('intro: comet recedes into the vanishing point and fades into the stars', () => {
  const g = H + 0.8;
  const d = Math.hypot(cometScreen(g)[0] - foeScreen(g)[0], cometScreen(g)[1] - foeScreen(g)[1]);
  assert(d < 25, `comet ${d.toFixed(1)} px from the vanishing point`);
  assert(intro.cometVisibility(H + ci.fadeOut.afterHandoffSeconds + ci.fadeOut.seconds) === 0, 'comet fully faded');
  return `at handoff+0.8 s the comet is ${d.toFixed(1)} px from the vanishing point; camera turn ${(geo.turnAngle * 180 / Math.PI).toFixed(1)}°`;
});

test('intro off restores the original opening', () => {
  const off = createIntroTimeline(config, timeline, { enabled: false });
  assert(off.handoffAt === 0 && off.turnProgress(0) === 1 && off.starReveal(0) === 1, 'neutral intro');
  assert(timeline.speedAt(-0.05) === config.flight.openingSpeed, 'flight starts already moving');
  // The flight after the handoff is the same flight: same peak, hold, slowdown and end state.
  const end = timeline.phases.flightEnd;
  assert(Math.abs(timeline.speedAt(end - 1) - iFlight.speedAt(end - 1)) < 1e-9, 'identical slowdown');
  return 'flight phases identical; only the approach to t = 0 differs';
});

test('intro: frame-rate independent at 60 and 120 Hz', () => {
  const at = (hz, seconds) => {
    let g = 0;
    for (let i = 0; i < seconds * hz; i++) g += 1 / hz;
    return { comet: cometScreen(g), turn: intro.turnProgress(g), field: iCamera.frameAt(g - H).field };
  };
  const a = at(60, 3);
  const b = at(120, 3);
  const diff = Math.max(
    Math.abs(a.comet[0] - b.comet[0]),
    Math.abs(a.comet[1] - b.comet[1]),
    Math.abs(a.turn - b.turn),
    Math.abs(a.field - b.field),
  );
  assert(diff < 1e-6, `differ by ${diff}`);
  return `max difference ${diff.toExponential(1)}`;
});

const failed = results.filter((r) => !r.ok);
document.title = failed.length ? 'FAIL' : 'PASS';
document.getElementById('results').innerHTML = results
  .map((r) => `<span class="${r.ok ? 'pass' : 'fail'}">${r.ok ? 'PASS' : 'FAIL'}</span> ${r.name}${r.note ? `\n     ${r.note}` : ''}`)
  .join('\n');
window.__testResults = results;
