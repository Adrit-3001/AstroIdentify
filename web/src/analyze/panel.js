/**
 * Result details beside the image: what happened, where the image points, what each marker
 * means, and the identified objects. Built with DOM nodes (never innerHTML), so catalogue
 * names and file names are always shown as text.
 */

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style') el.setAttribute('style', v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const SWATCH = {
  object: (color) =>
    `<svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><g stroke="${color}" stroke-width="2" stroke-linecap="round"><path d="M10 2v5M10 13v5M2 10h5M13 10h5"/></g></svg>`,
  star: () =>
    '<svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><circle cx="10" cy="10" r="6" fill="none" stroke="#96cdff" stroke-width="1.6"/></svg>',
  detection: () =>
    '<svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><circle cx="10" cy="10" r="4.5" fill="none" stroke="#96a2be" stroke-width="1.4" opacity="0.8"/></svg>',
};

function swatch(kind, colors) {
  const span = h('span', { class: 'legend__swatch' });
  // Static, trusted SVG markup (colours come from the server's fixed category palette).
  span.innerHTML = kind === 'object' ? SWATCH.object(colors?.[0] || '#e6e6e6') : SWATCH[kind]();
  return span;
}

function formatArcmin(value) {
  if (value === null || value === undefined) return null;
  return value >= 120 ? `${(value / 60).toFixed(2)}°` : `${value.toFixed(1)}′`;
}

function formatSeconds(s) {
  if (s === null || s === undefined) return null;
  if (s < 60) return `${Math.round(s)} s`;
  return `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, '0')} s`;
}

/** Prettier RA/Dec than the pipeline's "18h53m32.95s" / "+33d01m38.4s". */
function prettyAngle(text) {
  return text
    .replace(/(\d)h/, '$1ʰ ')
    .replace(/(\d)m(?=\d)/, '$1ᵐ ')
    .replace(/(\d)s$/, '$1ˢ')
    .replace(/(\d)d/, '$1° ')
    .replace(/(\d)m(?=\d)/, '$1′ ')
    .replace(/(\d)s$/, '$1″');
}

/**
 * @param {{outcome: HTMLElement, details: HTMLElement}} panel  above / below the options
 * @param {object} result   the result for the current display options, from the server
 * @param {object} job      job status (filename, elapsed)
 * @param {(id: number|null) => void} onHighlight
 */
export function renderPanel(panel, result, job, onHighlight) {
  const { outcome, sky, counts, legend, objects, notes } = result;
  const customised = result.view && !result.view.default;
  const nodes = [];

  panel.outcome.replaceChildren(
    h(
      'section',
      { class: `panel__outcome panel__outcome--${outcome.kind}` },
      h('h2', { class: 'panel__title' }, outcome.title),
      h('p', {}, outcome.message),
      outcome.markers ? h('p', { class: 'panel__markers' }, outcome.markers) : null,
      outcome.detail ? h('p', { class: 'panel__detail' }, `Details: ${outcome.detail}`) : null,
    ),
  );

  if (sky) {
    const field =
      sky.field_width_arcmin && sky.field_height_arcmin
        ? `${formatArcmin(sky.field_width_arcmin)} × ${formatArcmin(sky.field_height_arcmin)}`
        : null;
    nodes.push(
      h(
        'section',
        { class: 'panel__section' },
        h('h3', {}, 'Where it points'),
        sky.check ? h('p', { class: `panel__hint${sky.confirmed ? '' : ' panel__hint--warn'}` }, sky.check) : null,
        h(
          'dl',
          { class: 'facts' },
          h('dt', {}, 'Right ascension'),
          h('dd', {}, prettyAngle(sky.ra)),
          h('dt', {}, 'Declination'),
          h('dd', {}, prettyAngle(sky.dec)),
          field ? [h('dt', {}, 'Field of view'), h('dd', {}, field)] : null,
          sky.pixel_scale_arcsec
            ? [h('dt', {}, 'Scale'), h('dd', {}, `${sky.pixel_scale_arcsec.toFixed(2)}″ per pixel`)]
            : null,
        ),
      ),
    );
  }

  if (legend.length) {
    nodes.push(
      h(
        'section',
        { class: 'panel__section' },
        h('h3', {}, 'What the markers mean'),
        h(
          'ul',
          { class: 'legend' },
          legend.map((item) =>
            h(
              'li',
              {},
              swatch(item.kind, item.colors),
              h('div', {}, h('strong', {}, item.label), h('span', {}, item.detail)),
            ),
          ),
        ),
      ),
    );
  }

  const hiddenObjects = (counts.objects_total || 0) - objects.length;
  if (!objects.length && hiddenObjects > 0) {
    nodes.push(
      h(
        'section',
        { class: 'panel__section' },
        h('h3', {}, 'Catalogued objects'),
        h('p', { class: 'panel__hint' }, `${hiddenObjects} hidden by your choices above.`),
      ),
    );
  }
  if (objects.length) {
    const list = h(
      'ul',
      { class: 'objects' },
      objects.map((o) => {
        const li = h(
          'li',
          { tabindex: '0', 'data-id': o.id },
          h('span', { class: 'objects__dot', style: `--c: ${o.color}` }),
          h(
            'div',
            {},
            h(
              'p',
              { class: 'objects__name' },
              o.name,
              o.common_name ? h('span', {}, ` · ${o.common_name}`) : null,
            ),
            h(
              'p',
              { class: 'objects__type' },
              o.type,
              o.magnitude ? ` · mag ${o.magnitude.value.toFixed(1)} (${o.magnitude.band})` : null,
            ),
            o.support_text
              ? h('p', { class: `objects__support objects__support--${o.support}` }, o.support_text)
              : null,
            !o.centre_in_image ? h('p', { class: 'objects__note' }, 'Centred outside the frame; it extends into it.') : null,
          ),
        );
        if (o.explanation) li.title = o.explanation;
        li.addEventListener('mouseenter', () => onHighlight(o.id));
        li.addEventListener('focus', () => onHighlight(o.id));
        li.addEventListener('mouseleave', () => onHighlight(null));
        li.addEventListener('blur', () => onHighlight(null));
        return li;
      }),
    );
    const labelled = counts.objects_labelled;
    let hint = 'Hover one to find it on the image.';
    if (customised && labelled < objects.length) {
      hint = `${labelled || 'None'} named with your choices (only objects the image shows can be named); all are marked. Hover one to find it.`;
    } else if (labelled < objects.length) {
      hint = labelled
        ? `${labelled} with image evidence are named on the image; all are marked. Hover one to find it.`
        : 'None is clearly visible in the image, so they are marked but not named. Hover one to find it.';
    }
    nodes.push(
      h(
        'section',
        { class: 'panel__section' },
        h('h3', {}, `Catalogued objects (${objects.length})`),
        h('p', { class: 'panel__hint' }, hint),
        hiddenObjects > 0 ? h('p', { class: 'panel__hint' }, `${hiddenObjects} more hidden by your choices above.`) : null,
        list,
      ),
    );
  }

  const facts = [
    h('dt', {}, 'File'),
    h('dd', {}, job.filename || result.filename),
    h('dt', {}, 'Size'),
    h('dd', {}, `${result.image.width} × ${result.image.height} pixels`),
    counts.detections ? [h('dt', {}, 'Points of light'), h('dd', {}, String(counts.detections))] : null,
    counts.matched_stars ? [h('dt', {}, 'Stars matched'), h('dd', {}, String(counts.matched_stars))] : null,
    job.elapsed_seconds ? [h('dt', {}, 'Analysis time'), h('dd', {}, formatSeconds(job.elapsed_seconds))] : null,
  ];
  nodes.push(
    h(
      'section',
      { class: 'panel__section' },
      h('h3', {}, 'This image'),
      h('dl', { class: 'facts' }, facts),
      notes.length ? notes.map((n) => h('p', { class: 'panel__hint' }, n)) : null,
    ),
  );

  panel.details.replaceChildren(...nodes);
}
