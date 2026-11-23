/**
 * Field stars and composed foreground stars, drawn as instanced trail quads.
 *
 * Field stars fill a box that wraps in all three axes around the camera. The volume is
 * homogeneous, so the sky looks statistically the same at every moment of the flight: the
 * first frame, the peak and the settled view are one continuous sky. Each trail is the segment
 * between the star's projected position one exposure ago and now, capped in length.
 */
import { createRandom } from '../random.js';
import { createProgram, GLSL_TO_CLIP } from './gl.js';

const FLOATS_PER_STAR = 9; // x, y, z | brightness, sizeJitter, kind | r, g, b
const BOX_MARGIN = 1.18; // lateral box size beyond the visible frustum at the far plane

const VERTEX = /* glsl */ `#version 300 es
precision highp float;
layout(location = 0) in vec2 aCorner;
layout(location = 1) in vec3 aStar;   // lateral x, y and settled depth (relative to settled camera)
layout(location = 2) in vec3 aLook;   // intrinsic brightness, size jitter, kind (0 field, 1 foreground)
layout(location = 3) in vec3 aColor;

uniform vec2 uViewport, uCenter;
uniform float uFocal, uPixelRatio;
uniform vec3 uFieldCam, uFieldCamPrev, uApproachCam, uApproachCamPrev;
uniform vec2 uBox, uBoxCenter;
uniform mat3 uRot, uRotPrev;     // world -> camera rotation now / one exposure earlier
uniform float uRevealCut;        // stars dimmer than this are hidden (intro: brightest first)
uniform float uNear, uFar, uNearFade, uFarFade;
uniform float uRefDepth, uFalloff, uGlobal;
uniform vec2 uSizeRange;
uniform float uMaxTrail, uEnergy, uSpikeLen, uMaxApparent;

out vec2 vLocal;      // position along / across the trail, device px (0 = tail)
out vec2 vFromHead;   // screen offset from the star's current position, device px
out float vLen, vSigma, vAlpha, vKind, vSpike, vRadius, vStreak;
out vec3 vColor;

${GLSL_TO_CLIP}

void main() {
  bool fg = aLook.z > 0.5;
  vec3 cam = fg ? uApproachCam : uFieldCam;
  vec3 camPrev = fg ? uApproachCamPrev : uFieldCamPrev;
  vec3 rel;
  float fade = 1.0;
  float brightness;
  if (fg) {
    rel = vec3(aStar.xy - cam.xy, aStar.z - cam.z);
    brightness = aLook.x * pow(aStar.z / max(rel.z, 1e-3), uFalloff);
  } else {
    float range = uFar - uNear;
    float z = uNear + mod(aStar.z - cam.z - uNear, range);
    rel = vec3(mod(aStar.xy - cam.xy - uBoxCenter + uBox, 2.0 * uBox) - uBox + uBoxCenter, z);
    fade = smoothstep(uNear, uNear + uNearFade, z) * (1.0 - smoothstep(uFar - uFarFade, uFar, z));
    brightness = aLook.x * pow(uRefDepth / z, uFalloff);
  }
  // One exposure earlier the camera was at camPrev: the star was this much farther / offset.
  vec3 relPrev = vec3(rel.xy + (cam.xy - camPrev.xy), rel.z + (cam.z - camPrev.z));

  brightness *= fade;
  brightness *= smoothstep(uRevealCut - 0.15, uRevealCut + 0.15, brightness) * uGlobal;
  // Into camera space: the camera may be turned (comet intro); identity otherwise.
  vec3 eye = uRot * rel;
  vec3 eyePrev = uRotPrev * relPrev;
  if (eye.z < uNear * 0.5 || brightness < 0.003) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0);  // outside clip space: culled
    return;
  }

  vec2 head = uCenter + uFocal * eye.xy / eye.z;
  vec2 tail = uCenter + uFocal * eyePrev.xy / max(eyePrev.z, uNear * 0.5);
  vec2 d = head - tail;
  float len = length(d);
  // Soft cap: linear up to the knee, then eases into uMaxTrail, so a trail reaching the cap
  // never stops growing abruptly.
  float knee = 0.75 * uMaxTrail;
  if (len > knee) {
    float span = uMaxTrail - knee;
    float soft = knee + span * (1.0 - exp(-(len - knee) / span));
    d *= soft / len;
    len = soft;
  }
  vec2 dir = len > 1e-4 ? d / len : vec2(1.0, 0.0);
  tail = head - d;

  // Brighter stars render larger, like a real exposure; keep at least ~0.5 px so motion
  // never aliases, conserving flux for stars that would be smaller.
  float sigma = mix(uSizeRange.x, uSizeRange.y, clamp(sqrt(brightness / 2.6), 0.0, 1.0)) * aLook.y;
  float minSigma = 0.5 * uPixelRatio;
  if (sigma < minSigma) {
    brightness *= (sigma * sigma) / (minSigma * minSigma);
    sigma = minSigma;
  }
  // Light spread along a trail dims it (energyConservation 1 = physical).
  float alpha = brightness * mix(1.0, (2.0 * sigma) / (2.0 * sigma + len), uEnergy);

  float radius = 3.2 * sigma;
  if (fg) radius = max(radius * 3.0, uSpikeLen);
  float along = mix(-radius, len + radius, aCorner.x * 0.5 + 0.5);
  float across = aCorner.y * radius;
  vec2 pos = tail + dir * along + vec2(-dir.y, dir.x) * across;

  vLocal = vec2(along, across);
  vFromHead = pos - head;
  vLen = len;
  vSigma = sigma;
  vAlpha = min(alpha, uMaxApparent);
  vKind = fg ? 1.0 : 0.0;
  vRadius = radius;
  // Taper only applies once the trail is clearly longer than the star (no half-moon points).
  vStreak = smoothstep(0.0, 4.0 * sigma, len);
  vSpike = 1.0 / (1.0 + len / (2.0 * sigma));  // spikes and halo fade while streaking
  vColor = aColor;
  gl_Position = toClip(pos, uViewport);
}`;

const FRAGMENT = /* glsl */ `#version 300 es
precision highp float;
in vec2 vLocal, vFromHead;
in float vLen, vSigma, vAlpha, vKind, vSpike, vRadius, vStreak;
in vec3 vColor;
uniform float uTailBrightness, uTailWidth, uHalo, uSpikeLen, uSpikeStrength, uPixelRatio;
out vec4 outColor;

void main() {
  float clamped = clamp(vLocal.x, 0.0, vLen);
  float t = vLen > 1e-3 ? clamped / vLen : 1.0;          // 0 at tail, 1 at head
  float sigma = vSigma * mix(mix(1.0, uTailWidth, vStreak), 1.0, t);
  vec2 dv = vec2(vLocal.x - clamped, vLocal.y);
  float core = exp(-dot(dv, dv) / (2.0 * sigma * sigma));
  float intensity = core * mix(mix(1.0, uTailBrightness, vStreak), 1.0, t * t);

  if (vKind > 0.5) {
    float r = length(vFromHead);
    // Window to exactly zero before the quad edge so the glow never shows a square.
    float window = 1.0 - smoothstep(0.45 * vRadius, 0.95 * vRadius, r);
    float halo = exp(-r / (vSigma * 3.5)) * uHalo * vSpike * window;
    float w = 0.55 * uPixelRatio;
    float sx = exp(-abs(vFromHead.y) / w) * max(0.0, 1.0 - abs(vFromHead.x) / uSpikeLen);
    float sy = exp(-abs(vFromHead.x) / w) * max(0.0, 1.0 - abs(vFromHead.y) / uSpikeLen);
    intensity += halo + (sx * sx + sy * sy) * uSpikeStrength * vSpike * window;
  }
  outColor = vec4(vColor * vAlpha * intensity, 1.0);
}`;

/**
 * @param {WebGL2RenderingContext} gl
 * @param {typeof import('../config.js').LANDING_CONFIG} config
 */
export function createStarfield(gl, config, quad, { starCountScale }) {
  const { program, uniforms } = createProgram(gl, VERTEX, FRAGMENT, 'starfield');
  const instanceBuffer = gl.createBuffer();
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
  const stride = FLOATS_PER_STAR * 4;
  [3, 3, 3].forEach((size, i) => {
    const loc = i + 1;
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, stride, i * 3 * 4);
    gl.vertexAttribDivisor(loc, 1);
  });
  gl.bindVertexArray(null);

  const s = config.stars;
  let instanceCount = 0;
  let box = [0, 0];
  let boxCenter = [0, 0];
  let builtFor = null; // { tanRange, layoutName, width, height, field }

  /**
   * Field stars for a homogeneous box covering, at the far plane, every direction the camera
   * looks during the opening (tanRange), with a margin. The box may be off-centre when the
   * camera starts turned away from the flight heading.
   */
  function buildField(view, tanRange) {
    const rng = createRandom(s.seed);
    const far = s.farDepth;
    box = [
      ((tanRange.x[1] - tanRange.x[0]) / 2) * far * BOX_MARGIN,
      ((tanRange.y[1] - tanRange.y[0]) / 2) * far * BOX_MARGIN,
    ];
    boxCenter = [((tanRange.x[0] + tanRange.x[1]) / 2) * far, ((tanRange.y[0] + tanRange.y[1]) / 2) * far];
    const range = far - s.nearDepth;
    // Stars per volume chosen so ~visibleCount fall in the settled frustum (volume A*far/3).
    const frustumArea = 4 * view.halfTan[0] * view.halfTan[1] * far * far;
    const visible = s.visibleCount * starCountScale;
    const count = Math.round((visible * 3 * 4 * box[0] * box[1] * range) / (frustumArea * far));
    const data = [];
    for (let i = 0; i < count; i++) {
      const x = boxCenter[0] + rng.range(-box[0], box[0]);
      const y = boxCenter[1] + rng.range(-box[1], box[1]);
      const z = rng.range(s.nearDepth, s.farDepth);
      const b = Math.min(s.maxBrightness, s.minBrightness * (1 - rng.next()) ** (-1 / s.brightnessSlope));
      const { color } = rng.weighted(s.palette);
      data.push(x, y, z, b * s.brightness, rng.range(0.85, 1.15), 0, ...color);
    }
    return data;
  }

  function buildForeground(view) {
    const data = [];
    for (const star of view.layout.foregroundStars) {
      const [x, y] = view.lateralAt(view.toPx(star), star.depth);
      data.push(x, y, star.depth, star.brightness, 1.25, 1, ...star.color);
    }
    return data;
  }

  const covers = (outer, inner) =>
    outer[0] <= inner[0] + 0.02 * Math.abs(inner[0]) && outer[1] >= inner[1] - 0.02 * Math.abs(inner[1]);

  /**
   * (Re)build instances if the view outgrew the box or the composition changed.
   * @param {{x: number[], y: number[]}} [tanRange] directions seen during the opening
   */
  function prepare(view, tanRange) {
    const need = tanRange ?? {
      x: [-view.halfTan[0], view.halfTan[0]],
      y: [-view.halfTan[1], view.halfTan[1]],
    };
    const needsField =
      !builtFor ||
      !covers(builtFor.tanRange.x, need.x) ||
      !covers(builtFor.tanRange.y, need.y) ||
      view.layoutName !== builtFor.layoutName;
    const needsForeground =
      needsField || view.width !== builtFor.width || view.height !== builtFor.height;
    if (!needsForeground) return;
    if (needsField) builtFor = { tanRange: need, field: buildField(view, need) };
    Object.assign(builtFor, { layoutName: view.layoutName, width: view.width, height: view.height });
    const all = new Float32Array([...builtFor.field, ...buildForeground(view)]);
    instanceCount = all.length / FLOATS_PER_STAR;
    gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, all, gl.STATIC_DRAW);
  }

  /**
   * @param {{field: number[], fieldPrev: number[], approach: number[], approachPrev: number[]}} cams
   * @param {{rotation: number[], rotationPrev: number[], revealCut: number}} look
   */
  function draw(view, cams, globalBrightness, look) {
    const pr = view.pixelRatio;
    const t = config.trails;
    const fg = config.foregroundStars;
    gl.useProgram(program);
    gl.uniform2f(uniforms.uViewport, view.width, view.height);
    gl.uniform2f(uniforms.uCenter, view.center[0], view.center[1]);
    gl.uniform1f(uniforms.uFocal, view.focal);
    gl.uniform1f(uniforms.uPixelRatio, pr);
    gl.uniform3fv(uniforms.uFieldCam, cams.field);
    gl.uniform3fv(uniforms.uFieldCamPrev, cams.fieldPrev);
    gl.uniform3fv(uniforms.uApproachCam, cams.approach);
    gl.uniform3fv(uniforms.uApproachCamPrev, cams.approachPrev);
    gl.uniform2f(uniforms.uBox, box[0], box[1]);
    gl.uniform2f(uniforms.uBoxCenter, boxCenter[0], boxCenter[1]);
    gl.uniformMatrix3fv(uniforms.uRot, false, look.rotation);
    gl.uniformMatrix3fv(uniforms.uRotPrev, false, look.rotationPrev);
    gl.uniform1f(uniforms.uRevealCut, look.revealCut);
    gl.uniform1f(uniforms.uNear, s.nearDepth);
    gl.uniform1f(uniforms.uFar, s.farDepth);
    gl.uniform1f(uniforms.uNearFade, s.nearFadeDepth);
    gl.uniform1f(uniforms.uFarFade, s.farFadeDepth);
    gl.uniform1f(uniforms.uRefDepth, s.referenceDepth);
    gl.uniform1f(uniforms.uFalloff, s.depthFalloff);
    gl.uniform1f(uniforms.uGlobal, globalBrightness);
    gl.uniform2f(uniforms.uSizeRange, s.sizeRangePx[0] * pr, s.sizeRangePx[1] * pr);
    gl.uniform1f(
      uniforms.uMaxTrail,
      Math.min(t.maxLengthPx * pr, t.maxLengthShortSideFraction * view.shortSide),
    );
    gl.uniform1f(uniforms.uEnergy, t.energyConservation);
    gl.uniform1f(uniforms.uMaxApparent, s.maxApparentBrightness);
    gl.uniform1f(uniforms.uTailBrightness, t.tailBrightness);
    gl.uniform1f(uniforms.uTailWidth, t.tailWidth);
    gl.uniform1f(uniforms.uHalo, fg.haloStrength);
    gl.uniform1f(uniforms.uSpikeLen, fg.spikeLengthPx * pr);
    gl.uniform1f(uniforms.uSpikeStrength, fg.spikeStrength);
    gl.bindVertexArray(vao);
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, instanceCount);
    gl.bindVertexArray(null);
  }

  return { prepare, draw, get instanceCount() { return instanceCount; } };
}
