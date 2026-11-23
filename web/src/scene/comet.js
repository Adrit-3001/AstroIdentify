/**
 * The comet: a small bright nucleus, a soft coma and a tapered particle tail laid along the
 * path the comet has just travelled. Positions come from the intro geometry; this module only
 * projects them through the current camera rotation and draws gaussian sprites additively.
 */
import { apply } from '../math3.js';
import { createRandom } from '../random.js';
import { createProgram, GLSL_TO_CLIP } from './gl.js';

const FLOATS = 6; // x, y, sigma (device px) | r, g, b (already scaled by intensity)

const VERTEX = /* glsl */ `#version 300 es
precision highp float;
layout(location = 0) in vec2 aCorner;
layout(location = 1) in vec3 aSprite;
layout(location = 2) in vec3 aColor;
uniform vec2 uViewport;
out vec2 vOffset;
out float vSigma;
out vec3 vColor;
${GLSL_TO_CLIP}
void main() {
  vOffset = aCorner * aSprite.z * 3.0;
  vSigma = aSprite.z;
  vColor = aColor;
  gl_Position = toClip(aSprite.xy + vOffset, uViewport);
}`;

const FRAGMENT = /* glsl */ `#version 300 es
precision highp float;
in vec2 vOffset;
in float vSigma;
in vec3 vColor;
out vec4 outColor;
void main() {
  float d2 = dot(vOffset, vOffset);
  float g = exp(-d2 / (2.0 * vSigma * vSigma));
  float r = sqrt(d2) / (3.0 * vSigma);
  outColor = vec4(vColor * g * (1.0 - smoothstep(0.75, 1.0, r)), 1.0);
}`;

/**
 * @param {WebGL2RenderingContext} gl
 * @param {typeof import('../config.js').LANDING_CONFIG} config
 */
export function createComet(gl, config, quad) {
  const c = config.cometIntro;
  const { program, uniforms } = createProgram(gl, VERTEX, FRAGMENT, 'comet');
  const capacity = c.tail.particleCount + 2;
  const data = new Float32Array(capacity * FLOATS);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, data.byteLength, gl.DYNAMIC_DRAW);
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.enableVertexAttribArray(1);
  gl.vertexAttribPointer(1, 3, gl.FLOAT, false, FLOATS * 4, 0);
  gl.vertexAttribDivisor(1, 1);
  gl.enableVertexAttribArray(2);
  gl.vertexAttribPointer(2, 3, gl.FLOAT, false, FLOATS * 4, 12);
  gl.vertexAttribDivisor(2, 1);
  gl.bindVertexArray(null);

  // Per-particle constants: starting place along the tail and sideways scatter.
  const rng = createRandom(c.tail.seed);
  const particles = Array.from({ length: c.tail.particleCount }, (_, i) => ({
    phase: (i + rng.next()) / c.tail.particleCount,
    scatter: (rng.next() + rng.next() + rng.next() - 1.5) / 1.5, // ~gaussian in -1..1
    size: rng.range(0.8, 1.2),
  }));

  /**
   * @param {ReturnType<typeof import('../intro/geometry.js').createIntroGeometry>} geometry
   * @param {number[]} rot world -> camera rotation (column-major)
   * @param {{g: number, visibility: number, glow: number}} state
   */
  function draw(view, geometry, rot, { g, visibility, glow }) {
    if (visibility <= 0.001) return;
    // Sizes are specified for a ~900 px short side and scale down on small screens.
    const pr = view.pixelRatio * Math.min(1, Math.max(0.55, view.shortSide / view.pixelRatio / 900));
    const ref = geometry.referenceDepth;
    const project = (p) => {
      const q = apply(rot, p);
      if (q[2] < 0.2) return null;
      return [view.center[0] + (view.focal * q[0]) / q[2], view.center[1] + (view.focal * q[1]) / q[2], q[2]];
    };
    let count = 0;
    const push = (x, y, sigma, color, intensity) => {
      const o = count * FLOATS;
      data[o] = x;
      data[o + 1] = y;
      data[o + 2] = sigma;
      data[o + 3] = color[0] * intensity;
      data[o + 4] = color[1] * intensity;
      data[o + 5] = color[2] * intensity;
      count++;
    };

    const head = project(geometry.positionAt(g));
    if (!head) return;
    const glowNorm = c.glow.intensity > 0 ? glow / c.glow.intensity : 0;

    // Tail: particles flow from the nucleus back along the travelled arc.
    const sNow = geometry.arcAt(g);
    const L = geometry.tailLength;
    for (const p of particles) {
      const u = (p.phase + g * c.tail.flowPerSecond) % 1; // 0 at the nucleus, 1 at the tail end
      const s = sNow - u * L;
      const a = project(geometry.pointAtArc(s));
      const b = project(geometry.pointAtArc(s + L * 0.02));
      if (!a || !b) continue;
      const k = Math.min(1.6, ref / a[2]);
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const len = Math.hypot(dx, dy) || 1;
      const spread = p.scatter * c.tail.spreadPx * pr * k * u ** 0.8;
      const x = a[0] - (dy / len) * spread;
      const y = a[1] + (dx / len) * spread;
      const sigma = Math.max(0.6 * pr, (c.tail.headSizePx + (c.tail.endSizePx - c.tail.headSizePx) * u) * pr * k * p.size);
      const fade = (1 - u) ** c.tail.taper * Math.min(1, u / 0.04) * Math.sqrt(Math.min(1, k));
      push(x, y, sigma, c.tail.color, c.tail.brightness * fade * visibility);
    }

    // Coma, then nucleus: both shrink and dim as the comet recedes, so it fades into the stars.
    // Size and brightness fall off gently with distance so the comet stays readable while it
    // pulls away, then becomes a star-like point.
    const k = Math.min(1.6, ref / head[2]);
    const dim = Math.min(1, k ** 0.25);
    push(
      head[0],
      head[1],
      Math.max(1.2 * pr, c.coma.radiusPx * pr * k ** 0.7 * (1 + c.glow.comaGrowth * glowNorm)),
      c.coma.color,
      c.coma.brightness * (1 + 0.6 * glow) * dim * visibility,
    );
    push(
      head[0],
      head[1],
      Math.max(0.6 * pr, c.nucleus.radiusPx * pr * Math.sqrt(k)),
      c.nucleus.color,
      c.nucleus.brightness * (1 + glow) * dim * visibility,
    );

    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, data, 0, count * FLOATS);
    gl.useProgram(program);
    gl.uniform2f(uniforms.uViewport, view.width, view.height);
    gl.bindVertexArray(vao);
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, count);
    gl.bindVertexArray(null);
  }

  return { draw };
}
