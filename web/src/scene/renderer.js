/**
 * Scene renderer: owns the WebGL2 context, the view and the intro geometry, and draws one frame
 * from a camera frame plus a few per-frame look values. It knows nothing about time; main.js
 * evaluates the timelines and passes the results in.
 */
import { createIntroGeometry } from '../intro/geometry.js';
import { IDENTITY } from '../math3.js';
import { createComet } from './comet.js';
import { createView } from './composition.js';
import { createDeepSky } from './deepsky.js';
import { createQuad } from './gl.js';
import { createStarfield } from './starfield.js';

/**
 * @param {HTMLCanvasElement} canvas
 * @param {typeof import('../config.js').LANDING_CONFIG} config
 * @param {{starCountScale: number, maxPixelRatio: number, textureSize: number}} quality
 * @param {{introEnabled: boolean}} options
 */
export function createRenderer(canvas, config, quality, { introEnabled }) {
  const gl = canvas.getContext('webgl2', {
    alpha: false,
    antialias: false,
    depth: false,
    stencil: false,
    premultipliedAlpha: false,
    powerPreference: 'high-performance',
  });
  if (!gl) throw new Error('WebGL2 is not available');

  const quad = createQuad(gl);
  const starfield = createStarfield(gl, config, quad, quality);
  const deepSky = createDeepSky(gl, config, quad, quality);
  const comet = introEnabled ? createComet(gl, config, quad) : null;
  let view = null;
  let intro = null;

  /** Match the drawing buffer to the canvas's CSS size and recompose if needed. */
  function resize(cssWidth, cssHeight, devicePixelRatio) {
    const pixelRatio = Math.min(devicePixelRatio || 1, quality.maxPixelRatio);
    const width = Math.max(1, Math.round(cssWidth * pixelRatio));
    const height = Math.max(1, Math.round(cssHeight * pixelRatio));
    if (view && view.width === width && view.height === height) return view;
    canvas.width = width;
    canvas.height = height;
    view = createView(config, width, height, pixelRatio);
    intro = introEnabled ? createIntroGeometry(config, view) : null;
    starfield.prepare(view, intro?.tanRange);
    deepSky.prepare(view);
    return view;
  }

  /**
   * @param {ReturnType<ReturnType<typeof import('../camera.js').createCamera>['frameAt']>} cam
   * @param {object} look  per-frame values from the timelines
   * @param {number} look.approachDistance
   * @param {number} look.brightness       overall star brightness
   * @param {number} [look.starReveal]     0..1, brightest stars first
   * @param {number} [look.deepSky]        0..1 galaxy / nebula visibility
   * @param {{g: number, visibility: number, glow: number}} [look.comet]
   */
  function render(cam, look) {
    const [bgR, bgG, bgB] = config.background.color;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, view.width, view.height);
    gl.clearColor(bgR, bgG, bgB, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE); // light adds up, so draw order never matters

    const rotation = intro ? intro.rotationAt(cam.turn) : IDENTITY;
    const rotationPrev = intro ? intro.rotationAt(cam.turnPrev) : IDENTITY;

    // Drawn even while invisible during the intro hold, so the GPU pipeline is warm before the
    // turn and nothing hitches when the sky fades in.
    const deepSkyLevel = look.brightness * (look.deepSky ?? 1);
    deepSky.draw(view, cam.approach, cam.lateral, look.approachDistance, deepSkyLevel, rotation);

    const [sx, sy] = view.slope;
    const cams = {
      field: [sx * cam.field + cam.lateral[0], sy * cam.field + cam.lateral[1], cam.field],
      fieldPrev: [
        sx * cam.fieldPrev + cam.lateralPrev[0],
        sy * cam.fieldPrev + cam.lateralPrev[1],
        cam.fieldPrev,
      ],
      approach: [sx * cam.approach + cam.lateral[0], sy * cam.approach + cam.lateral[1], cam.approach],
      approachPrev: [
        sx * cam.approachPrev + cam.lateralPrev[0],
        sy * cam.approachPrev + cam.lateralPrev[1],
        cam.approachPrev,
      ],
    };
    // Reveal threshold: from above the brightest star down to below the faintest.
    // At reveal 0 nothing shows (black opening); the first stars to pass the cut are the
    // brightest, then the threshold sweeps down through the whole field.
    const reveal = look.starReveal ?? 1;
    const gate = Math.min(1, reveal / 0.08);
    starfield.draw(view, cams, look.brightness * gate, {
      rotation,
      rotationPrev,
      revealCut: 2.0 - 2.4 * reveal,
    });

    if (comet && intro && look.comet) comet.draw(view, intro, rotation, look.comet);
  }

  return {
    gl,
    resize,
    render,
    get view() {
      return view;
    },
    get intro() {
      return intro;
    },
    get starCount() {
      return starfield.instanceCount;
    },
  };
}
