/**
 * View-dependent geometry of the comet intro.
 *
 * - The camera's starting orientation is derived, not configured: it is the rotation that
 *   makes the flight heading (the settled focus of expansion) appear where the comet's outbound
 *   ray is on the starting screen. Turning the camera from there to identity therefore "follows
 *   the comet": its outbound path ends up at the flight's vanishing point.
 * - The comet path is a time-keyed cubic Hermite spline, in world axes, relative to the camera
 *   position (the comet always leads the camera). Its keys are: the i's dot (start, at rest),
 *   the loop around the word (word units, mapped through the measured word box) and the exit
 *   (view fractions).
 *
 * World axes are the settled camera's: x right, y down, z forward.
 */
import { apply, cross, dot, length, normalize, rotation, scale, sub } from '../math3.js';

const TABLE_STEP = 1 / 500; // s

/**
 * Where the wordmark sits, in view fractions, when it could not be measured (tests, or no DOM):
 * a centred word about 36% of the width wide.
 */
export function defaultWordAnchor(view) {
  const hw = Math.min(0.42, (0.18 * 1440) / (view.width / view.pixelRatio));
  const hh = (hw * view.width) / view.height / 5.4;
  return { tittle: [0.5 + 0.62 * hw, 0.5 - 0.55 * hh], box: { cx: 0.5, cy: 0.5, hw, hh } };
}

/** All comet keyframes in view fractions + depth: detach point, loop, exit. */
export function cometKeys(config, anchor) {
  const p = config.cometIntro.path;
  const { cx, cy, hw, hh } = anchor.box;
  return [
    { at: p.detachAtSeconds, x: anchor.tittle[0], y: anchor.tittle[1], depth: p.loopDepth, rest: true },
    ...p.loop.map((k) => ({ at: k.at, x: cx + k.x * hw, y: cy + k.y * hh, depth: k.depth ?? p.loopDepth })),
    ...p.exit.map((k) => ({ ...k })),
  ];
}

/**
 * @param {typeof import('../config.js').LANDING_CONFIG} config
 * @param {ReturnType<typeof import('../scene/composition.js').createView>} view
 * @param {ReturnType<typeof defaultWordAnchor>} [anchor] measured wordmark (see wordmark.js)
 */
export function createIntroGeometry(config, view, anchor = defaultWordAnchor(view)) {
  const c = config.cometIntro;
  const keys = cometKeys(config, anchor);
  const { width, height, focal, center } = view;

  /** Camera-space direction (z = 1) of a screen point given in view fractions. */
  const viewRay = (x, y) => [(x * width - center[0]) / focal, (y * height - center[1]) / focal, 1];

  // Starting orientation: rotate the flight heading onto the outbound ray (last keyframe).
  const heading = normalize([view.slope[0], view.slope[1], 1]);
  const last = keys[keys.length - 1];
  const outbound = normalize(viewRay(last.x, last.y));
  const axisRaw = cross(heading, outbound);
  const axis = length(axisRaw) > 1e-9 ? normalize(axisRaw) : [0, 1, 0];
  const angle = Math.acos(Math.min(1, Math.max(-1, dot(heading, outbound))));

  /** World -> camera rotation for a turn progress p (0 = start, 1 = aligned with the flight). */
  const rotationAt = (p) => rotation(axis, angle * (1 - p));
  const startToWorld = rotation(axis, -angle); // inverse of rotationAt(0)

  // Keyframes in world axes (relative to the camera position).
  const times = keys.map((k) => k.at);
  const points = keys.map((k) => apply(startToWorld, scale(viewRay(k.x, k.y), k.depth)));
  const n = points.length;
  // Non-uniform Catmull-Rom tangents (units per second) keep velocity continuous at keys. The
  // detach key has zero velocity: the dot eases off the i from rest.
  const tangents = points.map((_, i) => {
    if (keys[i].rest) return [0, 0, 0];
    const a = Math.max(0, i - 1);
    const b = Math.min(n - 1, i + 1);
    return scale(sub(points[b], points[a]), 1 / (times[b] - times[a]));
  });

  /** Comet position (world axes, camera-relative) at intro time g; linear beyond the ends. */
  function positionAt(g) {
    if (g <= times[0]) return points[0].map((v, j) => v + tangents[0][j] * (g - times[0]));
    if (g >= times[n - 1]) return points[n - 1].map((v, j) => v + tangents[n - 1][j] * (g - times[n - 1]));
    let i = 0;
    while (g > times[i + 1]) i++;
    const h = times[i + 1] - times[i];
    const s = (g - times[i]) / h;
    const s2 = s * s;
    const s3 = s2 * s;
    const h00 = 2 * s3 - 3 * s2 + 1;
    const h10 = s3 - 2 * s2 + s;
    const h01 = 3 * s2 - 2 * s3;
    const h11 = s3 - s2;
    return [0, 1, 2].map(
      (j) => h00 * points[i][j] + h10 * h * tangents[i][j] + h01 * points[i + 1][j] + h11 * h * tangents[i + 1][j],
    );
  }

  // Arc-length table so the tail can follow the travelled curve at a constant length.
  const tableStart = times[0] - 2;
  const tableEnd = times[n - 1] + 6;
  const count = Math.ceil((tableEnd - tableStart) / TABLE_STEP) + 1;
  const tablePos = new Array(count);
  const tableArc = new Float64Array(count);
  for (let i = 0; i < count; i++) {
    tablePos[i] = positionAt(tableStart + i * TABLE_STEP);
    if (i > 0) tableArc[i] = tableArc[i - 1] + length(sub(tablePos[i], tablePos[i - 1]));
  }

  /** Distance travelled along the path by intro time g, u. */
  function arcAt(g) {
    const x = Math.min(count - 1, Math.max(0, (g - tableStart) / TABLE_STEP));
    const i = Math.min(count - 2, Math.floor(x));
    return tableArc[i] + (tableArc[i + 1] - tableArc[i]) * (x - i);
  }

  /** Path position at arc length s (clamped to the table). */
  function pointAtArc(s) {
    if (s <= 0) return tablePos[0];
    if (s >= tableArc[count - 1]) return tablePos[count - 1];
    let lo = 0;
    let hi = count - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (tableArc[mid] < s) lo = mid;
      else hi = mid;
    }
    const span = tableArc[hi] - tableArc[lo] || 1;
    const f = (s - tableArc[lo]) / span;
    return [0, 1, 2].map((j) => tablePos[lo][j] + (tablePos[hi][j] - tablePos[lo][j]) * f);
  }

  const startTime = times[0];
  const startArc = arcAt(startTime);

  // Closest-approach depth sets the reference scale for comet sizes and tail length.
  const referenceDepth = Math.min(...keys.map((k) => k.depth));
  const tailLength = c.tail.lengthShortSide * (view.shortSide / focal) * referenceDepth;

  /**
   * Angular coverage (tan x, tan y ranges in world axes) of everything the camera sees during
   * the turn, so the star volume can be sized to have no empty regions.
   */
  const [hx, hy] = view.halfTan;
  const tanRange = { x: [-hx, hx], y: [-hy, hy] };
  for (const p of [0, 0.5]) {
    const toWorld = rotation(axis, -angle * (1 - p));
    for (const sx of [-1, 1]) {
      for (const sy of [-1, 1]) {
        const d = apply(toWorld, [sx * hx, sy * hy, 1]);
        if (d[2] <= 0.05) continue;
        tanRange.x = [Math.min(tanRange.x[0], d[0] / d[2]), Math.max(tanRange.x[1], d[0] / d[2])];
        tanRange.y = [Math.min(tanRange.y[0], d[1] / d[2]), Math.max(tanRange.y[1], d[1] / d[2])];
      }
    }
  }

  return {
    rotationAt,
    turnAngle: angle,
    keys,
    anchor,
    /** When the comet leaves the i, and the arc length there (its tail never reaches back past it). */
    startTime,
    startArc,
    positionAt,
    arcAt,
    pointAtArc,
    referenceDepth,
    tailLength,
    tanRange,
  };
}
