/**
 * Camera motion. Everything is expressed relative to the *settled* camera (the camera at the
 * end of the flight), so the composed sky is exactly what the visitor sees once it settles.
 *
 * Forward travel and ambient sway are pure functions of timeline time. The pointer parallax is
 * a spring integrated in fixed 1/240 s steps, so it also behaves the same at 60 Hz and 120 Hz.
 */

const SPRING_STEP = 1 / 240;

/**
 * @param {typeof import('./config.js').LANDING_CONFIG} config
 * @param {ReturnType<typeof import('./timeline.js').createTimeline>} timeline
 */
export function createCamera(config, timeline, { reducedMotion = false, intro = null } = {}) {
  const drift = config.ambientDrift;
  const parallaxCfg = config.pointerParallax;
  const exposure = config.trails.exposureSeconds;
  const swayEnabled = !reducedMotion || config.reducedMotion.ambientDrift;
  const parallaxEnabled = parallaxCfg.enabled && parallaxCfg.strength > 0 && !reducedMotion;

  const spring = { x: 0, y: 0, vx: 0, vy: 0, targetX: 0, targetY: 0, accumulator: 0 };

  function swayAt(t) {
    if (!swayEnabled) return [0, 0];
    const w = timeline.ambientWeightAt(t);
    if (w === 0) return [0, 0];
    // Phase measured from the slowdown start so the sway is continuous with the flight.
    const tau = t - timeline.phases.slowdownStart;
    const [ax, ay] = drift.swayAmplitude;
    const [px, py] = drift.swayPeriodSeconds;
    return [w * ax * Math.sin((2 * Math.PI * tau) / px), w * ay * Math.sin((2 * Math.PI * tau) / py)];
  }

  /** Pointer position in -1..1 (x right, y down); null when the pointer leaves. */
  function setPointer(nx, ny) {
    if (!parallaxEnabled) return;
    spring.targetX = nx === null ? 0 : nx * parallaxCfg.strength;
    spring.targetY = ny === null ? 0 : ny * parallaxCfg.strength;
  }

  /** Advance the parallax spring by real elapsed seconds. */
  function stepSpring(dt) {
    if (!parallaxEnabled) return;
    const k = parallaxCfg.stiffness;
    const c = 2 * parallaxCfg.dampingRatio * Math.sqrt(k);
    spring.accumulator += Math.min(dt, 0.25);
    while (spring.accumulator >= SPRING_STEP) {
      spring.accumulator -= SPRING_STEP;
      // Semi-implicit Euler: stable for these stiffness values.
      spring.vx += (-k * (spring.x - spring.targetX) - c * spring.vx) * SPRING_STEP;
      spring.vy += (-k * (spring.y - spring.targetY) - c * spring.vy) * SPRING_STEP;
      spring.x += spring.vx * SPRING_STEP;
      spring.y += spring.vy * SPRING_STEP;
    }
  }

  /**
   * Camera state at flight time t, plus the state one exposure earlier (for trails).
   * Flight time is negative during the comet intro.
   * - field:    forward offset for field stars (includes the ambient forward drift), u.
   * - approach: forward offset for deep-sky objects and foreground stars; eases to exactly 0.
   * - lateral:  sway + parallax offset shared by everything, u.
   * - turn:     comet-intro camera turn progress (0 = turned toward the comet, 1 = aligned).
   * All forward offsets are <= 0 during the flight and 0 for the settled camera.
   */
  function frameAt(t) {
    const tPrev = t - exposure;
    const ambientWeight = timeline.ambientWeightAt(t);
    const [sx, sy] = swayAt(t);
    const [psx, psy] = swayAt(tPrev);
    const px = spring.x * ambientWeight;
    const py = spring.y * ambientWeight;
    return {
      field: timeline.distanceAt(t) - timeline.flightDistance,
      fieldPrev: timeline.distanceAt(tPrev) - timeline.flightDistance,
      approach: timeline.approachAt(t) - timeline.approachDistance,
      approachPrev: timeline.approachAt(tPrev) - timeline.approachDistance,
      lateral: [sx + px, sy + py],
      // Parallax is deliberately identical in both: pointer moves should not smear trails.
      lateralPrev: [psx + px, psy + py],
      turn: intro ? intro.turnProgress(t + intro.handoffAt) : 1,
      turnPrev: intro ? intro.turnProgress(tPrev + intro.handoffAt) : 1,
    };
  }

  return { frameAt, setPointer, stepSpring, parallaxEnabled };
}
