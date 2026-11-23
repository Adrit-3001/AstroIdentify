/**
 * The single animation timeline. Camera travel, trail exposure, deep-sky approach, ambient
 * sway weighting and text reveal are all pure functions of one time value `t` (seconds since
 * the first rendered frame), so they can never drift out of sync and look identical at any
 * frame rate.
 */

const clamp01 = (x) => Math.min(1, Math.max(0, x));
/** Quintic smoothstep: zero first and second derivative at both ends (no felt start/end). */
const smootherstep = (a, b, x) => {
  const s = clamp01((x - a) / (b - a || 1e-9));
  return s * s * s * (s * (s * 6 - 15) + 10);
};

/** Easing functions on [0, 1] -> [0, 1]. */
export const EASINGS = {
  linear: () => (s) => s,
  sineInOut: () => (s) => 0.5 - 0.5 * Math.cos(Math.PI * s),
  cubicInOut: () => (s) => (s < 0.5 ? 4 * s * s * s : 1 - (-2 * s + 2) ** 3 / 2),
  quadOut: () => (s) => 1 - (1 - s) * (1 - s),
  cubicOut: () => (s) => 1 - (1 - s) ** 3,
  /**
   * Front-loaded braking with a long glide: warp time so most progress happens early, then
   * smooth it so the slope is zero at both ends (no jolt entering, no visible stop leaving).
   */
  longTail:
    ({ tailExponent = 2.6 } = {}) =>
    (s) => {
      const warped = 1 - (1 - s) ** tailExponent;
      return warped * warped * (3 - 2 * warped);
    },
};

function easingFor(name, params) {
  const factory = EASINGS[name];
  if (!factory) throw new Error(`Unknown easing "${name}". Use one of: ${Object.keys(EASINGS)}`);
  return factory(params);
}

/**
 * @param {typeof import('./config.js').LANDING_CONFIG} config
 * @param {{ leadIn?: boolean }} options  leadIn: the comet intro precedes the flight, so the
 *   camera starts from rest before t = 0 instead of already moving.
 */
export function createTimeline(config, { leadIn = false } = {}) {
  const f = config.flight;
  const v0 = f.openingSpeed;
  const vPeak = f.peakSpeed;
  const vAmb = f.ambientSpeed;
  const tAccel = Math.max(1e-4, f.accelerationSeconds);
  const tHold = Math.max(0, f.peakHoldSeconds);
  const tSlow = Math.max(1e-4, f.slowdownSeconds);
  const rise = easingFor(f.accelerationEasing);
  const settle = easingFor(f.slowdownEasing, { tailExponent: f.slowdownTailExponent });

  const slowdownStart = tAccel + tHold;
  const flightEnd = slowdownStart + tSlow;

  // Lead-in (comet intro): speed rises from rest as v0 * ((t + R) / R)^n over t in [-R, 0].
  // It reaches v0 with acceleration a0 = n * v0 / R, and the rise below starts with exactly
  // that acceleration (cubic Hermite), so speed and acceleration are continuous: one launch.
  const h = config.cometIntro?.handoff ?? {};
  const leadR = Math.max(1e-3, h.leadInSeconds ?? 0.8);
  const leadN = Math.max(1.01, h.leadInExponent ?? 2.5);
  const leadInDistance = (v0 * leadR) / (leadN + 1);
  // Clamp the start slope so the Hermite rise never overshoots the peak speed.
  const riseStartSlope = Math.min((leadN * v0) / leadR, (3 * (vPeak - v0)) / tAccel);
  const hermiteRise = (s) => {
    const s2 = s * s;
    const s3 = s2 * s;
    return (
      v0 * (2 * s3 - 3 * s2 + 1) + riseStartSlope * tAccel * (s3 - 2 * s2 + s) + vPeak * (3 * s2 - 2 * s3)
    );
  };

  /**
   * Forward speed, u/s. Before t = 0 the camera was already moving at the opening speed, or,
   * with the comet intro, was at rest and is accelerating out of the lead-in.
   */
  function speedAt(t) {
    if (t <= 0) {
      if (!leadIn) return v0;
      return t <= -leadR ? 0 : v0 * ((t + leadR) / leadR) ** leadN;
    }
    if (t < tAccel) return leadIn ? hermiteRise(t / tAccel) : v0 + (vPeak - v0) * rise(t / tAccel);
    if (t < slowdownStart) return vPeak;
    if (t < flightEnd) return vAmb + (vPeak - vAmb) * (1 - settle((t - slowdownStart) / tSlow));
    return vAmb;
  }

  // Integrate the speed curve once (Simpson's rule on a fine grid) so distance is exact to
  // ~1e-9 u and independent of how often frames are drawn.
  const STEP = 1 / 1000;
  const samples = Math.ceil(flightEnd / STEP);
  const cumulative = new Float64Array(samples + 1);
  for (let i = 0; i < samples; i++) {
    const a = i * STEP;
    const b = Math.min(flightEnd, a + STEP);
    cumulative[i + 1] =
      cumulative[i] + ((b - a) / 6) * (speedAt(a) + 4 * speedAt((a + b) / 2) + speedAt(b));
  }
  const flightDistance = cumulative[samples];

  /** Camera travel along the flight direction since t = 0, u. Negative before t = 0. */
  function distanceAt(t) {
    if (t <= 0) {
      if (!leadIn) return v0 * t;
      if (t <= -leadR) return -leadInDistance;
      return leadInDistance * (((t + leadR) / leadR) ** (leadN + 1) - 1);
    }
    if (t >= flightEnd) return flightDistance + vAmb * (t - flightEnd);
    const x = t / STEP;
    const i = Math.min(samples - 1, Math.floor(x));
    const frac = x - i;
    // Within one 1 ms step speed is ~linear, so a quadratic correction keeps this exact.
    const a = i * STEP;
    const va = speedAt(a);
    const vb = speedAt(Math.min(flightEnd, a + STEP));
    const h = frac * STEP;
    return cumulative[i] + va * h + ((vb - va) / (2 * STEP)) * h * h;
  }

  /**
   * "Approach" travel: the flight minus the ambient baseline. Its speed eases to exactly
   * zero, so deep-sky objects and the composed foreground stars glide into place and stay
   * there, while the field stars keep the very slow ambient forward drift.
   */
  const approachDistance = flightDistance - vAmb * flightEnd;
  function approachAt(t) {
    // The ambient baseline only runs once the flight has started, so nothing drifts while the
    // camera is still during the intro.
    return Math.min(distanceAt(t) - vAmb * Math.max(0, t), approachDistance);
  }

  /** 0 before the slowdown, 1 when the flight has fully settled. */
  function slowdownProgress(t) {
    return clamp01((t - slowdownStart) / tSlow);
  }

  /** Global sky brightness: starts visible (never blank), reaches 1 shortly after opening. */
  function openingBrightnessAt(t) {
    const d = f.openingBrightnessSeconds;
    if (d <= 0) return 1;
    const s = clamp01(t / d);
    return f.openingBrightness + (1 - f.openingBrightness) * (1 - (1 - s) ** 2);
  }

  /**
   * Weight (0–1) of the ambient sway and pointer parallax. It fades in across the end of the
   * flight (progress may run past 1) so there is no single moment where the drift "starts".
   */
  function ambientWeightAt(t) {
    const d = config.ambientDrift;
    const progress = (t - slowdownStart) / tSlow;
    return smootherstep(d.fadeInFromSlowdownProgress, d.fadeInToSlowdownProgress, progress);
  }

  const reveal = config.textReveal;
  const revealStart = slowdownStart + tSlow * reveal.startAtSlowdownProgress;

  /** Reveal progress (0–1, eased) of the index-th text element. */
  function revealAt(t, index) {
    const start = revealStart + index * reveal.staggerSeconds;
    const s = clamp01((t - start) / Math.max(1e-4, reveal.durationSeconds));
    return 1 - (1 - s) ** 3;
  }

  /** Time at which element `count - 1` has fully appeared. */
  function revealEnd(count) {
    return revealStart + (count - 1) * reveal.staggerSeconds + reveal.durationSeconds;
  }

  return {
    speedAt,
    distanceAt,
    approachAt,
    slowdownProgress,
    openingBrightnessAt,
    ambientWeightAt,
    revealAt,
    revealEnd,
    flightDistance,
    approachDistance,
    phases: { accelerationEnd: tAccel, slowdownStart, flightEnd, leadInStart: leadIn ? -leadR : 0 },
    leadIn,
    /** A time at which everything is settled: used for reduced motion. */
    settledTime: Math.max(flightEnd, revealEnd(3)) + 0.5,
  };
}
