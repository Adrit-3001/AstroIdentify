/**
 * Result viewer: the analysed image with its markers and labels.
 *
 * Markers are SVG in image-pixel coordinates (viewBox = the image's own size, and the frame
 * always has the image's aspect ratio), so they stay on their objects at any display size.
 * Marker sizes match the downloadable PNG, but never shrink below a readable size on small
 * screens. Labels are HTML in screen pixels, placed with the same rule as the PNG and
 * re-placed whenever the frame is resized.
 */
import { estimateRadius, labelText, markerBox, placeLabel } from './labels.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
// Smallest on-screen marker sizes (CSS px) so markers stay visible when the image is small.
const MIN_PX = { detection: 2.5, star: 4, objectInner: 4, objectOuter: 10 };
const LABEL_GAP_PX = 6;

function svg(tag, attrs = {}, parent = null) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  if (parent) parent.append(el);
  return el;
}

export function createViewer({ frame, image, markersSvg, labelsLayer }) {
  let result = null;
  let items = []; // { marker, el, labelEl }
  let resizeObserver = null;

  function clear() {
    markersSvg.replaceChildren();
    labelsLayer.replaceChildren();
    items = [];
  }

  /** Show a result over the image at `imageUrl`; resolves when the image has loaded. */
  function show(newResult, imageUrl, altText) {
    const { width: W, height: H } = newResult.image;
    frame.style.setProperty('--ar', `${W} / ${H}`);
    frame.style.setProperty('--ar-num', String(W / H));
    markersSvg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    markersSvg.setAttribute('preserveAspectRatio', 'none');
    image.alt = altText;
    image.width = W;
    image.height = H;
    draw(newResult);

    resizeObserver?.disconnect();
    resizeObserver = new ResizeObserver(() => layout());
    resizeObserver.observe(frame);

    return new Promise((resolve) => {
      image.onload = () => {
        layout();
        resolve();
      };
      image.onerror = () => resolve();
      image.src = imageUrl;
    });
  }

  /** Replace the markers and labels (same image, other display options). */
  function update(newResult) {
    draw(newResult);
    layout();
  }

  function draw(newResult) {
    result = newResult;
    clear();
    const layers = {
      detection: svg('g', { class: 'm-layer m-layer--detections' }, markersSvg),
      star: svg('g', { class: 'm-layer m-layer--stars' }, markersSvg),
      object: svg('g', { class: 'm-layer m-layer--objects' }, markersSvg),
    };
    for (const marker of result.markers) {
      const item = { marker, el: null, parts: {}, labelEl: null };
      if (marker.kind === 'detection' || marker.kind === 'star') {
        item.el = svg(
          'circle',
          {
            class: marker.kind === 'star' ? (marker.name ? 'm-star m-star--named' : 'm-star') : 'm-detection',
            cx: marker.x + 0.5,
            cy: marker.y + 0.5,
          },
          layers[marker.kind],
        );
        if (marker.name) svg('title', {}, item.el).textContent = marker.name;
      } else {
        const g = svg(
          'g',
          { class: marker.estimated ? 'm-object m-object--estimated' : 'm-object', 'data-id': marker.id },
          layers.object,
        );
        g.style.setProperty('--c', marker.color);
        svg('title', {}, g).textContent = `${marker.name}${marker.type ? ` — ${marker.type}` : ''}`;
        if (marker.footprint) {
          const pts = marker.footprint.map(([x, y]) => `${x + 0.5},${y + 0.5}`).join(' ');
          svg('polygon', { points: pts, class: 'm-object__outline' }, g);
        }
        if (marker.estimated && marker.centre_in_image) {
          // Not seen in the image: a dashed accuracy circle and a dot, never a crosshair.
          item.parts.circle = svg('circle', { class: 'm-object__estimate', cx: marker.x + 0.5, cy: marker.y + 0.5 }, g);
          item.parts.dot = svg('circle', { class: 'm-object__dot', cx: marker.x + 0.5, cy: marker.y + 0.5 }, g);
        } else if (marker.centre_in_image) {
          item.parts.arms = [0, 1, 2, 3].map(() => svg('line', { class: 'm-object__arm' }, g));
        }
        item.el = g;
      }
      if (marker.label) {
        const [first, second] = labelText(marker);
        const div = document.createElement('div');
        div.className = `label label--${marker.kind}`;
        if (marker.kind === 'object') {
          div.style.setProperty('--c', marker.color);
          div.dataset.id = marker.id;
        }
        const name = document.createElement('span');
        name.className = 'label__name';
        name.textContent = first;
        div.append(name);
        if (second) {
          const type = document.createElement('span');
          type.className = 'label__type';
          type.textContent = second;
          div.append(type);
        }
        labelsLayer.append(div);
        item.labelEl = div;
      }
      items.push(item);
    }
  }

  /** Marker sizes (image px) for the current display scale. */
  function sizes(scale) {
    const s = result.style;
    return {
      detectionRadius: Math.max(s.detection_radius, MIN_PX.detection / scale),
      starRadius: Math.max(s.star_radius, MIN_PX.star / scale),
      objectInner: Math.max(s.object_inner, MIN_PX.objectInner / scale),
      objectOuter: Math.max(s.object_outer, MIN_PX.objectOuter / scale),
    };
  }

  function layout() {
    if (!result) return;
    const { width: W, height: H } = result.image;
    const displayW = frame.clientWidth;
    const displayH = frame.clientHeight;
    if (!displayW || !displayH) return;
    const scale = displayW / W;
    const sz = sizes(scale);

    for (const { marker, el, parts } of items) {
      if (marker.kind === 'detection') el.setAttribute('r', sz.detectionRadius);
      else if (marker.kind === 'star') el.setAttribute('r', sz.starRadius);
      else if (parts.circle) {
        parts.circle.setAttribute('r', estimateRadius(marker, sz.objectOuter));
        parts.dot.setAttribute('r', Math.max(1.5, 2 / scale));
      } else if (parts.arms) {
        // Crosshair; with an outline, just a small centre tick (as in the PNG).
        const [inner, outer] = marker.footprint ? [0, sz.objectInner] : [sz.objectInner, sz.objectOuter];
        const cx = marker.x + 0.5;
        const cy = marker.y + 0.5;
        [[1, 0], [-1, 0], [0, 1], [0, -1]].forEach(([dx, dy], i) => {
          const arm = parts.arms[i];
          arm.setAttribute('x1', cx + dx * inner);
          arm.setAttribute('y1', cy + dy * inner);
          arm.setAttribute('x2', cx + dx * outer);
          arm.setAttribute('y2', cy + dy * outer);
        });
      }
    }

    // Labels in screen pixels, in the same order and with the same rule as the PNG.
    const placed = [];
    for (const { marker, labelEl } of items) {
      if (!labelEl) continue;
      const [x0, y0, x1, y1] = markerBox(marker, sz, W, H);
      const box = [(x0 + 0.5) * scale, (y0 + 0.5) * scale, (x1 + 0.5) * scale, (y1 + 0.5) * scale];
      const size = [labelEl.offsetWidth, labelEl.offsetHeight];
      const rect = placeLabel(box, size, placed, displayW, displayH, LABEL_GAP_PX);
      placed.push(rect);
      labelEl.style.transform = `translate(${rect[0].toFixed(1)}px, ${rect[1].toFixed(1)}px)`;
    }
  }

  function setMode(mode) {
    frame.classList.toggle('is-original', mode === 'original');
  }

  function highlight(id) {
    for (const el of markersSvg.querySelectorAll('.m-object.is-highlighted, .label.is-highlighted')) {
      el.classList.remove('is-highlighted');
    }
    for (const el of labelsLayer.querySelectorAll('.label.is-highlighted')) el.classList.remove('is-highlighted');
    if (id === null || id === undefined) {
      frame.classList.remove('has-highlight');
      return;
    }
    frame.classList.add('has-highlight');
    markersSvg.querySelector(`.m-object[data-id="${id}"]`)?.classList.add('is-highlighted');
    labelsLayer.querySelector(`.label[data-id="${id}"]`)?.classList.add('is-highlighted');
  }

  function reset() {
    resizeObserver?.disconnect();
    result = null;
    clear();
    image.removeAttribute('src');
  }

  return { show, update, setMode, highlight, reset, layout };
}
