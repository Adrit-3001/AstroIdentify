/**
 * Comet-intro timeline. Like the flight timeline, everything here is a pure function of `g`,
 * the seconds since the first frame, so it is frame-rate independent and can be frozen or
 * scrubbed. The flight's own time is `g - handoffAt`.
 *
 * When the intro is disabled this returns neutral values (no logo, no comet, camera aligned,
 * stars and deep sky fully visible, flight starting at g = 0): the original opening.
 */

const clamp01 = (x) => Math.min(1, Math.max(0, x));
const smootherstep = (a, b, x) => {
  const s = clamp01((x - a) / (b - a || 1e-9));
  return s * s * s * (s * (s * 6 - 15) + 10);
};

/**
 * Step response of a damped spring released from rest at x = 0 toward x = 1: zero velocity at
 * the start (a gentle turn-in), then an asymptotic catch-up (overshoots when dampingRatio < 1).
 */
export function springStep(time, stiffness, dampingRatio) {
  if (time <= 0) return 0;
  const w = Math.sqrt(stiffness);
  const z = dampingRatio;
  if (z * w * time > 30) return 1;
  if (Math.abs(z - 1) < 1e-6) return 1 - Math.exp(-w * time) * (1 + w * time);
  if (z < 1) {
    const wd = w * Math.sqrt(1 - z * z);
    return 1 - Math.exp(-z * w * time) * (Math.cos(wd * time) + (z / Math.sqrt(1 - z * z)) * Math.sin(wd * time));
  }
  const root = Math.sqrt(z * z - 1);
  const r1 = -w * (z - root);
  const r2 = -w * (z + root);
  return 1 + (r2 * Math.exp(r1 * time) - r1 * Math.exp(r2 * time)) / (r1 - r2);
}

/**
 * @param {typeof import('../config.js').LANDING_CONFIG} config
 * @param {ReturnType<typeof import('../timeline.js').createTimeline>} flight
 */
export function createIntroTimeline(config, flight, { enabled }) {
  if (!enabled) {
    return {
      enabled: false,
      handoffAt: 0,
      logoOpacity: () => 0,
      starReveal: () => 1,
      skyBrightness: (g) => flight.openingBrightnessAt(g),
      deepSkyVisibility: () => 1,
      turnProgress: () => 1,
      cometVisibility: () => 0,
      ignitionAt: () => 0,
      glowAt: () => 0,
      moments: {},
    };
  }

  const c = config.cometIntro;
  const handoffAt = c.handoff.flightStartsAtSeconds;
  const detach = c.path.detachAtSeconds;
  // The comet starts pulling away when the loop ends.
  const departure = c.path.loop.length ? c.path.loop[c.path.loop.length - 1].at : detach;
  const igniteStart = detach + c.ignition.delaySeconds;
  const turnStart = departure + c.cameraFollow.delaySeconds;
  const emergeStart = turnStart + c.stars.emergeOffsetSeconds;
  const deepSkyStart = turnStart + c.deepSky.fadeInOffsetSeconds;
  const glowSigma = c.glow.durationSeconds / 2.3548; // FWHM -> standard deviation

  /** 0 = camera looking where it started; 1 = aligned with the flight heading. */
  function turnProgress(g) {
    return springStep(g - turnStart, c.cameraFollow.stiffness, c.cameraFollow.dampingRatio);
  }

  function starReveal(g) {
    return smootherstep(emergeStart, emergeStart + c.stars.emergeSeconds, g);
  }

  return {
    enabled: true,
    handoffAt,
    logoOpacity: (g) => 1 - smootherstep(c.logo.fadeStartSeconds, c.logo.fadeStartSeconds + c.logo.fadeSeconds, g),
    /** 0 = no stars; rising, the brightest stars appear first, then the whole field. */
    starReveal,
    skyBrightness: (g) => c.stars.initialBrightness + (1 - c.stars.initialBrightness) * starReveal(g),
    deepSkyVisibility: (g) => smootherstep(deepSkyStart, deepSkyStart + c.deepSky.fadeInSeconds, g),
    turnProgress,
    cometVisibility: (g) => {
      const start = handoffAt + c.fadeOut.afterHandoffSeconds;
      return 1 - smootherstep(start, start + c.fadeOut.seconds, g);
    },
    /** 0 before detach; rises to 1 as the detached dot becomes the comet. */
    ignitionAt: (g) => smootherstep(igniteStart, igniteStart + c.ignition.seconds, g),
    /** Extra nucleus brightness (0 = none) from the shine. */
    glowAt: (g) => c.glow.intensity * Math.exp(-0.5 * ((g - c.glow.peakAtSeconds) / glowSigma) ** 2),
    moments: { detach, departure, turnStart, handoffAt, leadInStart: handoffAt - c.handoff.leadInSeconds },
  };
}
