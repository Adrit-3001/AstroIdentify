/**
 * View geometry and composition: which layout applies, the lens, and conversions between the
 * settled screen ("view" fractions from config) and world space relative to the settled camera.
 *
 * Screen space is device pixels with y pointing down; world x/y follow the same directions, and
 * world z is depth in front of the camera.
 */

/**
 * @param {typeof import('../config.js').LANDING_CONFIG} config
 * @param {number} width  drawing-buffer width, device px
 * @param {number} height drawing-buffer height, device px
 * @param {number} pixelRatio device px per CSS px
 */
export function createView(config, width, height, pixelRatio) {
  const aspect = width / height;
  const layoutName = aspect < config.composition.narrowBelowAspectRatio ? 'narrow' : 'wide';
  const layout = config.composition[layoutName];
  const shortSide = Math.min(width, height);
  const halfFov = ((config.camera.fovShortSideDegrees * Math.PI) / 180) / 2;
  const focal = shortSide / 2 / Math.tan(halfFov);
  const center = [width / 2, height / 2];

  /** Settled screen position (view fractions) -> device px. */
  const toPx = ({ x, y }) => [x * width, y * height];

  /** World lateral position, relative to the settled camera, of a screen point at a depth. */
  const lateralAt = (pxPoint, depth) => [
    ((pxPoint[0] - center[0]) * depth) / focal,
    ((pxPoint[1] - center[1]) * depth) / focal,
  ];

  // The camera travels along (slope.x, slope.y, 1), so the focus of expansion is fixed on screen.
  const foe = toPx(layout.focusOfExpansion);
  const slope = [(foe[0] - center[0]) / focal, (foe[1] - center[1]) / focal];

  return {
    width,
    height,
    pixelRatio,
    aspect,
    layoutName,
    layout,
    shortSide,
    focal,
    center,
    slope,
    toPx,
    lateralAt,
    /** tan of the half field of view on each axis. */
    halfTan: [width / 2 / focal, height / 2 / focal],
  };
}
