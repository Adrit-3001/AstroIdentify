/**
 * The intro wordmark's detaching dot (the tittle above the lowercase i).
 *
 * The wordmark stays real HTML text so it is sharp and painted on the first frame. To let the
 * dot leave, this module:
 *   1. finds the dot's exact shape by drawing the same glyph, in the same computed font, on an
 *      offscreen canvas and scanning it (works with whichever system font is rendering);
 *   2. at detach, clips the dot off the in-word i and shows an overlay copy of the glyph that is
 *      clipped to only its dot. Together they render exactly the original i, so nothing
 *      jumps; then the overlay dot moves along the comet path.
 * It also reports the word's ink box, so the comet's loop can be defined around the word.
 */

const SCAN_SCALE = 4;

/** Dot centre, radius and the cut line between dot and stem, in CSS px from glyph origin. */
function scanTittle(font, fontPx) {
  const canvas = document.createElement('canvas');
  const w = Math.ceil(fontPx * 1.0 * SCAN_SCALE);
  const h = Math.ceil(fontPx * 1.6 * SCAN_SCALE);
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) return null;
  const originX = fontPx * 0.3;
  const baseline = fontPx * 1.25;
  ctx.setTransform(SCAN_SCALE, 0, 0, SCAN_SCALE, 0, 0);
  ctx.font = font;
  ctx.fillStyle = '#fff';
  ctx.textBaseline = 'alphabetic';
  ctx.fillText('i', originX, baseline);
  const { data } = ctx.getImageData(0, 0, w, h);
  const rowInk = (y) => {
    for (let x = 0; x < w; x++) if (data[(y * w + x) * 4 + 3] > 40) return true;
    return false;
  };
  let top = -1;
  for (let y = 0; y < h && top < 0; y++) if (rowInk(y)) top = y;
  if (top < 0) return null;
  let gap = -1;
  for (let y = top; y < h && gap < 0; y++) if (!rowInk(y)) gap = y;
  let stem = -1;
  for (let y = gap; gap >= 0 && y < h && stem < 0; y++) if (rowInk(y)) stem = y;
  if (gap < 0 || stem < 0) return null; // dot touches the stem in this font: no clean split

  let sum = 0;
  let sx = 0;
  let sy = 0;
  let minX = w;
  let maxX = 0;
  for (let y = top; y < gap; y++) {
    for (let x = 0; x < w; x++) {
      const a = data[(y * w + x) * 4 + 3];
      if (!a) continue;
      sum += a;
      sx += a * x;
      sy += a * y;
      if (a > 40) [minX, maxX] = [Math.min(minX, x), Math.max(maxX, x)];
    }
  }
  return {
    cx: (sx / sum + 0.5) / SCAN_SCALE - originX,
    cyAbove: baseline - (sy / sum + 0.5) / SCAN_SCALE,
    radius: Math.max(maxX - minX + 1, gap - top) / 2 / SCAN_SCALE,
    cutAbove: baseline - (gap + stem) / 2 / SCAN_SCALE,
  };
}

/** Rough tittle metrics from font metrics alone, if the pixel scan is not possible. */
function estimateTittle(ctx, fontPx) {
  const x = ctx.measureText('x').actualBoundingBoxAscent || fontPx * 0.52;
  const i = ctx.measureText('i');
  const top = i.actualBoundingBoxAscent || fontPx * 0.74;
  const radius = Math.max(1, (top - x) * 0.32);
  return { cx: i.width / 2, cyAbove: top - radius, radius, cutAbove: x + (top - x) * 0.2 };
}

const rect = (el) => el.getBoundingClientRect();

/** @param {HTMLElement} markEl the .intro-mark element */
export function createWordmark(markEl) {
  const word = markEl.querySelector('.intro-mark__word');
  const glyph = markEl.querySelector('.intro-mark__i');
  const wordBaseline = word.querySelector('.intro-mark__baseline');
  const dot = markEl.querySelector('.intro-mark__tittle');
  const dotBaseline = dot.querySelector('.intro-mark__baseline');
  const measureCtx = document.createElement('canvas').getContext('2d');
  let layout = null;

  /**
   * Measure the word and its dot. Returns the comet anchor in view fractions of a viewport of
   * the given CSS size, or null if the wordmark is not displayed.
   * - tittle: the dot's centre [x, y]
   * - box: the word's ink box (centre cx, cy; half-size hw, hh)
   */
  function measure(viewWidth, viewHeight) {
    if (!word.isConnected || getComputedStyle(markEl).display === 'none') return null;
    glyph.style.clipPath = '';
    dot.style.transform = '';
    dot.style.clipPath = '';

    const cs = getComputedStyle(word);
    const fontPx = parseFloat(cs.fontSize);
    const font = `${cs.fontStyle} ${cs.fontWeight} ${fontPx}px ${cs.fontFamily}`;
    measureCtx.font = font;
    const t = scanTittle(font, fontPx) ?? estimateTittle(measureCtx, fontPx);

    const glyphBox = rect(glyph);
    const baseline = rect(wordBaseline).top;
    const wordBox = rect(word);
    const ink = measureCtx.measureText(word.textContent);
    const inkTop = baseline - (ink.actualBoundingBoxAscent || fontPx * 0.75);
    const inkBottom = baseline + (ink.actualBoundingBoxDescent || fontPx * 0.2);

    // In-word i: keep everything below the cut. Overlay copy: keep only what is above it.
    const pad = fontPx;
    const cutInGlyph = baseline - t.cutAbove - glyphBox.top;
    const dotBox = rect(dot);
    const dotBase = rect(dotBaseline).top;
    const cutInDot = dotBase - t.cutAbove - dotBox.top;
    layout = {
      tittle: [glyphBox.left + t.cx, baseline - t.cyAbove],
      radius: t.radius,
      // Moves the overlay so its glyph sits exactly on the in-word i.
      align: [glyphBox.left - dotBox.left, baseline - dotBase],
      glyphClip: `polygon(${-pad}px ${cutInGlyph}px, ${glyphBox.width + pad}px ${cutInGlyph}px, ${glyphBox.width + pad}px ${glyphBox.height + pad}px, ${-pad}px ${glyphBox.height + pad}px)`,
      dotClip: `polygon(${-pad}px ${-pad}px, ${dotBox.width + pad}px ${-pad}px, ${dotBox.width + pad}px ${cutInDot}px, ${-pad}px ${cutInDot}px)`,
    };
    dot.style.clipPath = layout.dotClip;
    return {
      tittle: [layout.tittle[0] / viewWidth, layout.tittle[1] / viewHeight],
      tittleRadius: [t.radius / viewWidth, t.radius / viewHeight],
      box: {
        cx: (wordBox.left + wordBox.right) / 2 / viewWidth,
        cy: (inkTop + inkBottom) / 2 / viewHeight,
        hw: wordBox.width / 2 / viewWidth,
        hh: (inkBottom - inkTop) / 2 / viewHeight,
      },
    };
  }

  /**
   * @param {boolean} detached  false: the original, untouched i
   * @param {number[]} position the dot's centre now, CSS px
   * @param {number} opacity    overlay dot opacity (it hands over to the comet as it ignites)
   */
  function update(detached, position, opacity) {
    if (!layout) return;
    if (!detached) {
      glyph.style.clipPath = '';
      dot.style.visibility = 'hidden';
      return;
    }
    glyph.style.clipPath = layout.glyphClip;
    dot.style.visibility = opacity > 0.002 ? 'visible' : 'hidden';
    dot.style.opacity = String(opacity);
    const dx = layout.align[0] + position[0] - layout.tittle[0];
    const dy = layout.align[1] + position[1] - layout.tittle[1];
    dot.style.transform = `translate3d(${dx.toFixed(2)}px, ${dy.toFixed(2)}px, 0)`;
  }

  return {
    measure,
    update,
    get tittle() {
      return layout?.tittle ?? null;
    },
  };
}
