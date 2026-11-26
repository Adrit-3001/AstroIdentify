/**
 * Label text and placement: the same rules as the downloadable PNG
 * (src/astroidentify/web/annotations.py: label_text, marker_box, label_candidates,
 * place_label), so the preview and the download name the same markers in the same way.
 * Here they run in screen pixels, so labels stay readable at any image size.
 */

/** [first line, second line or null] of a marker's label. */
export function labelText(marker) {
  if (marker.kind === 'object') {
    const name = marker.common_name ? `${marker.name} (${marker.common_name})` : marker.name;
    return [name, marker.type || null];
  }
  return [marker.name || '', null];
}

/**
 * Box a label is placed beside, in image pixels (pixel centres at integers).
 * `sizes` are the marker sizes actually drawn (image pixels).
 */
export function markerBox(marker, sizes, width, height) {
  if (marker.kind === 'object') {
    if (marker.footprint) {
      let pts = marker.footprint.filter(
        ([x, y]) => x >= -0.5 && x <= width - 0.5 && y >= -0.5 && y <= height - 0.5,
      );
      if (!pts.length) pts = marker.footprint;
      const xs = pts.map((p) => p[0]);
      const ys = pts.map((p) => p[1]);
      return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    }
    const r = sizes.objectOuter;
    return [marker.x - r, marker.y - r, marker.x + r, marker.y + r];
  }
  const r = sizes.starRadius;
  return [marker.x - r, marker.y - r, marker.x + r, marker.y + r];
}

function candidates([x0, y0, x1, y1], [tw, th], gap) {
  const cy = (y0 + y1) / 2 - th / 2;
  const cx = (x0 + x1) / 2 - tw / 2;
  const spots = [
    [x1 + gap, cy],
    [x0 - gap - tw, cy],
    [cx, y1 + gap],
    [cx, y0 - gap - th],
  ];
  for (const k of [1, -1, 2, -2]) {
    const dy = k * (th + gap);
    spots.push([x1 + gap, cy + dy], [x0 - gap - tw, cy + dy]);
  }
  return spots;
}

function overlapArea(a, b) {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]);
  const h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  return w > 0 && h > 0 ? w * h : 0;
}

/** Rectangle for a label: first free candidate spot inside the frame, else least overlap. */
export function placeLabel(box, size, placed, width, height, gap) {
  const [tw, th] = size;
  let best = null;
  let bestOverlap = Infinity;
  for (let [sx, sy] of candidates(box, size, gap)) {
    sx = Math.min(Math.max(sx, 0), Math.max(0, width - tw));
    sy = Math.min(Math.max(sy, 0), Math.max(0, height - th));
    const rect = [sx, sy, sx + tw, sy + th];
    const overlap = placed.reduce((sum, p) => sum + overlapArea(rect, p), 0);
    if (overlap === 0) return rect;
    if (overlap < bestOverlap) [best, bestOverlap] = [rect, overlap];
  }
  return best;
}
