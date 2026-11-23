/**
 * Deep-sky objects: one spiral galaxy, one diffuse nebula and a few small distant galaxies.
 *
 * Each object's appearance is generated procedurally on the GPU once, into a texture, then drawn
 * every frame as a billboard. Objects keep a fixed place in the same sky: during the flight they
 * approach (grow by `growthDuringFlight`) and glide to their composed position; afterwards they
 * only drift with depth-dependent parallax.
 */
import { apply, IDENTITY } from '../math3.js';
import { createRandom } from '../random.js';
import { createProgram, GLSL_NOISE, GLSL_TO_CLIP } from './gl.js';

const FULLSCREEN_VERTEX = /* glsl */ `#version 300 es
layout(location = 0) in vec2 aCorner;
out vec2 vUv;
void main() { vUv = aCorner; gl_Position = vec4(aCorner, 0.0, 1.0); }`;

/** Face-on spiral galaxy; inclination and rotation are applied when drawing. */
const GALAXY_FRAGMENT = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
uniform float uArms, uPitch;
uniform vec3 uCore, uArm, uStarForming, uDust;
out vec4 outColor;
${GLSL_NOISE}
void main() {
  vec2 p = vUv;
  float r = length(p);
  float theta = atan(p.y, p.x);
  float n = fbm(p * 3.2);
  float fine = fbm(p * 11.0 + 7.3);
  float phase = uArms * (theta - log(max(r, 0.015)) / tan(uPitch)) + n * 2.6;
  // Broad arms with mottled brightness, sitting on a continuous exponential disk.
  float arm = pow(0.5 + 0.5 * cos(phase), 1.8) * (0.35 + 1.3 * fine);
  float armZone = smoothstep(0.04, 0.16, r) * (1.0 - smoothstep(0.35, 0.95, r));
  float disk = exp(-r / 0.21);
  float bulge = exp(-pow(r / 0.05, 0.75));
  float clumps = pow(fbm(p * 18.0 + 3.1), 5.0) * 14.0;
  float lane = pow(0.5 + 0.5 * cos(phase - 0.85), 6.0) * smoothstep(0.06, 0.2, r)
             * (1.0 - smoothstep(0.4, 0.7, r)) * (0.6 + 0.8 * n);

  vec3 c = uCore * (bulge * 1.3 + disk * 0.55)
         + uArm * disk * armZone * arm * 1.6
         + uStarForming * disk * armZone * arm * clumps * 0.7
         + mix(uCore, uArm, 0.6) * 0.07 * exp(-r / 0.38);
  c = mix(c, c * uDust * 0.8, clamp(lane * 0.5, 0.0, 1.0));
  c *= 1.0 - smoothstep(0.7, 1.0, r);
  c = 1.0 - exp(-c * 1.5);  // soft shoulder instead of hard clipping
  outColor = vec4(c, 1.0);
}`;

/** Diffuse emission nebula: domain-warped fbm with dust lanes and a soft boundary. */
const NEBULA_FRAGMENT = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
uniform vec3 uHydrogen, uOxygen, uCoreColor;
out vec4 outColor;
${GLSL_NOISE}
void main() {
  vec2 p = vUv * 1.7;
  vec2 q = vec2(fbm(p), fbm(p + vec2(5.2, 1.3)));
  vec2 w = vec2(fbm(p + 3.2 * q + vec2(1.7, 9.2)), fbm(p + 3.2 * q + vec2(8.3, 2.8)));
  float f = fbm(p + 3.4 * w);
  float mask = 1.0 - smoothstep(0.15, 1.0, length(vUv * vec2(1.0, 1.3)));
  float density = smoothstep(0.34, 0.9, f) * mask;
  float filaments = pow(1.0 - abs(2.0 * fbm(p * 2.6 + q * 2.0) - 1.0), 5.0);
  float dust = smoothstep(0.56, 0.74, fbm(p * 2.2 + w * 2.4 + 4.0));
  vec3 tint = mix(uHydrogen, uOxygen, smoothstep(0.4, 0.85, w.x) * 0.75);
  vec3 c = tint * density * (0.75 + 0.9 * filaments)
         + uCoreColor * pow(density, 2.5) * 0.8
         + uHydrogen * 0.2 * mask * mask * mask;
  c *= 1.0 - dust * 0.45;
  outColor = vec4(c, 1.0);
}`;

const BILLBOARD_VERTEX = /* glsl */ `#version 300 es
precision highp float;
layout(location = 0) in vec2 aCorner;
uniform vec2 uViewport, uCenter, uHalfSize;
uniform float uAngle;
out vec2 vUv;
${GLSL_TO_CLIP}
void main() {
  vUv = aCorner * 0.5 + 0.5;
  vec2 c = aCorner * uHalfSize;
  float s = sin(uAngle), k = cos(uAngle);
  gl_Position = toClip(uCenter + vec2(k * c.x - s * c.y, s * c.x + k * c.y), uViewport);
}`;

const BILLBOARD_FRAGMENT = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
uniform sampler2D uTexture;
uniform vec3 uTint;
uniform float uIntensity;
out vec4 outColor;
void main() {
  vec3 c = texture(uTexture, vUv).rgb * uTint * uIntensity;
  // Tiny ordered dither breaks up 8-bit banding in faint gradients.
  float d = fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715))));
  outColor = vec4(c + d / 255.0 * step(0.0005, c.r + c.g + c.b), 1.0);
}`;

function bakeTexture(gl, quad, size, fragment, label, setUniforms) {
  const { program, uniforms } = createProgram(gl, FULLSCREEN_VERTEX, fragment, label);
  const texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, size, size, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
  const fbo = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
  gl.viewport(0, 0, size, size);
  gl.disable(gl.BLEND);
  gl.useProgram(program);
  setUniforms(uniforms);
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.deleteFramebuffer(fbo);
  gl.deleteProgram(program);
  gl.generateMipmap(gl.TEXTURE_2D);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return texture;
}

/**
 * @param {WebGL2RenderingContext} gl
 * @param {typeof import('../config.js').LANDING_CONFIG} config
 */
export function createDeepSky(gl, config, quad, { textureSize }) {
  const g = config.galaxy;
  const n = config.nebula;
  const deg = Math.PI / 180;

  const galaxyTexture = bakeTexture(gl, quad, textureSize, GALAXY_FRAGMENT, 'galaxy', (u) => {
    gl.uniform1f(u.uSeed, g.seed);
    gl.uniform1f(u.uArms, g.armCount);
    gl.uniform1f(u.uPitch, g.armPitchDegrees * deg);
    gl.uniform3fv(u.uCore, g.coreColor);
    gl.uniform3fv(u.uArm, g.armColor);
    gl.uniform3fv(u.uStarForming, g.starFormingColor);
    gl.uniform3fv(u.uDust, g.dustColor);
  });
  const nebulaTexture = bakeTexture(gl, quad, textureSize, NEBULA_FRAGMENT, 'nebula', (u) => {
    gl.uniform1f(u.uSeed, n.seed);
    gl.uniform3fv(u.uHydrogen, n.hydrogenColor);
    gl.uniform3fv(u.uOxygen, n.oxygenColor);
    gl.uniform3fv(u.uCoreColor, n.coreColor);
  });

  const { program, uniforms } = createProgram(gl, BILLBOARD_VERTEX, BILLBOARD_FRAGMENT, 'billboard');
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.bindVertexArray(null);

  let objects = [];

  /** Place every object in the settled sky for this view (back to front). */
  function prepare(view) {
    const layout = view.layout;
    const make = (spec, placement, extra) => {
      const px = view.toPx(placement);
      const diameter = placement.diameter * view.shortSide;
      return {
        texture: extra.texture,
        depth: spec.depth,
        growth: spec.growthDuringFlight,
        lateral: view.lateralAt(px, spec.depth),
        diameter,
        squash: extra.squash ?? 1,
        angle: extra.angle ?? 0,
        tint: extra.tint ?? [1, 1, 1],
        intensity: spec.brightness,
      };
    };
    const list = [
      make(n, layout.nebula, { texture: nebulaTexture, angle: n.rotationDegrees * deg }),
    ];
    // Distant galaxies: seeded positions, kept away from the text and the main galaxy.
    const dg = config.distantGalaxies;
    const rng = createRandom(dg.seed);
    const quiet = layout.quietZone;
    for (let placed = 0, tries = 0; placed < dg.count && tries < 400; tries++) {
      const x = rng.range(0.04, 0.96);
      const y = rng.range(0.05, 0.95);
      const size = rng.range(dg.sizeRangePx[0], dg.sizeRangePx[1]) * view.pixelRatio;
      const inclination = rng.range(20, 78);
      const angle = rng.range(0, 180);
      const tint = rng.next() < 0.5 ? [1, 0.95, 0.88] : [0.9, 0.95, 1.05];
      const qx = (x - quiet.x) / quiet.radiusX;
      const qy = (y - quiet.y) / quiet.radiusY;
      const gx = ((x - layout.galaxy.x) * view.width) / view.shortSide;
      const gy = ((y - layout.galaxy.y) * view.height) / view.shortSide;
      if (qx * qx + qy * qy < 1 || Math.hypot(gx, gy) < layout.galaxy.diameter * 0.7) continue;
      list.push(
        make(dg, { x, y, diameter: size / view.shortSide }, {
          texture: galaxyTexture,
          squash: Math.cos(inclination * deg),
          angle: angle * deg,
          tint,
        }),
      );
      placed++;
    }
    list.push(
      make(g, layout.galaxy, {
        texture: galaxyTexture,
        squash: Math.cos(g.inclinationDegrees * deg),
        angle: g.positionAngleDegrees * deg,
      }),
    );
    objects = list;
  }

  /**
   * @param {number} approach    approach offset (<= 0 in flight, 0 settled)
   * @param {number[]} lateral   sway + parallax offset, u
   * @param {number} approachDistance total approach travel of the flight, u
   * @param {number[]} rot world -> camera rotation (identity unless the camera is turned)
   */
  function draw(view, approach, lateral, approachDistance, globalBrightness, rot = IDENTITY) {
    gl.useProgram(program);
    gl.uniform2f(uniforms.uViewport, view.width, view.height);
    gl.uniform1i(uniforms.uTexture, 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindVertexArray(vao);
    for (const o of objects) {
      // Each object sees a scaled share of the flight so that it grows by exactly `growth`.
      const share = approachDistance > 0 ? ((o.growth - 1) * o.depth) / approachDistance : 0;
      const camZ = approach * share;
      const depth = o.depth - camZ;
      const camX = view.slope[0] * camZ + lateral[0];
      const camY = view.slope[1] * camZ + lateral[1];
      const eye = apply(rot, [o.lateral[0] - camX, o.lateral[1] - camY, depth]);
      if (eye[2] <= 1e-3) continue;
      const scale = o.depth / eye[2];
      const cx = view.center[0] + (view.focal * eye[0]) / eye[2];
      const cy = view.center[1] + (view.focal * eye[1]) / eye[2];
      const half = (o.diameter * scale) / 2;
      gl.bindTexture(gl.TEXTURE_2D, o.texture);
      gl.uniform2f(uniforms.uCenter, cx, cy);
      gl.uniform2f(uniforms.uHalfSize, half, half * o.squash);
      gl.uniform1f(uniforms.uAngle, o.angle);
      gl.uniform3fv(uniforms.uTint, o.tint);
      gl.uniform1f(uniforms.uIntensity, o.intensity * globalBrightness);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
    gl.bindVertexArray(null);
  }

  return { prepare, draw };
}
