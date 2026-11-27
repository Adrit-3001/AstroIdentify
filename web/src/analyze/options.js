/**
 * Display options beside the result: which kinds of marker to show, "name only the brightest",
 * and particular objects to name. The server decides what each choice shows (one rule for the
 * preview and the download, src/astroidentify/web/view.py); this module only collects the
 * choices and shows how each requested name was answered.
 *
 * Options: { types: string[] | null (null = every type), brightest: number | null, labels: string[] }
 */

export const DEFAULT_OPTIONS = Object.freeze({ types: null, brightest: null, labels: [], undetected: false });
const DEFAULT_BRIGHTEST = 5;
const MAX_BRIGHTEST = 100;
const MAX_LABELS = 20;

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

/** Options <-> URL query (also used in the page address after #job=...). */
export function optionsToParams(options, params = new URLSearchParams()) {
  if (options.types) params.set('types', options.types.join(','));
  if (options.brightest) params.set('brightest', String(options.brightest));
  for (const name of options.labels || []) params.append('label', name);
  if (options.undetected) params.set('undetected', '1');
  return params;
}

export function optionsFromParams(params) {
  const types = params.has('types') ? params.get('types').split(',').filter(Boolean) : null;
  const n = Number.parseInt(params.get('brightest') || '', 10);
  const brightest = Number.isFinite(n) && n >= 1 ? Math.min(n, MAX_BRIGHTEST) : null;
  const labels = params.getAll('label').filter((s) => s.trim()).slice(0, MAX_LABELS);
  const undetected = params.get('undetected') === '1';
  return { types, brightest, labels, undetected };
}

export function isDefault(options) {
  return options.types === null && !options.brightest && !options.labels.length && !options.undetected;
}

/** The server's answer for a requested name (its colour shows the outcome). */
function statusText(answer) {
  if (!answer) return 'Looking it up…';
  return answer.status === 'labelled' ? 'Named on the image.' : answer.message;
}

/**
 * @param {HTMLElement} container
 * @param {(options: object) => void} onChange  called with the new options after each change
 */
export function createOptions(container, onChange) {
  let options = { ...DEFAULT_OPTIONS };
  let available = []; // [{key, label, count, color}]
  let answers = []; // the server's answer for each requested name
  let numberTimer = null;

  const typesList = h('div', { class: 'options__types', role: 'group', 'aria-labelledby': 'opt-types' });
  const brightestToggle = h('input', { type: 'checkbox', id: 'opt-brightest' });
  const brightestInput = h('input', {
    type: 'number',
    id: 'opt-brightest-n',
    min: 1,
    max: MAX_BRIGHTEST,
    step: 1,
    value: DEFAULT_BRIGHTEST,
    inputmode: 'numeric',
    'aria-label': 'How many to name',
  });
  const undetectedToggle = h('input', { type: 'checkbox', id: 'opt-undetected' });
  const requestInput = h('input', {
    type: 'text',
    id: 'opt-request',
    autocomplete: 'off',
    spellcheck: 'false',
    maxlength: 100,
    placeholder: 'e.g. M 57 or Albireo',
  });
  const requestButton = h('button', { type: 'submit', class: 'btn btn--small' }, 'Add');
  const requestList = h('ul', { class: 'requests', 'aria-live': 'polite' });
  const requestError = h('p', { class: 'options__error', role: 'alert', hidden: true });
  const resetButton = h('button', { type: 'button', class: 'options__reset' }, 'Reset to default');
  const typeHint = h('p', { class: 'panel__hint' });

  const form = h(
    'form',
    { class: 'options__request' },
    h('label', { for: 'opt-request', class: 'options__label' }, 'Also name a particular object'),
    h('div', { class: 'options__row' }, requestInput, requestButton),
    h(
      'p',
      { class: 'panel__hint' },
      'A name, catalogue ID or Gaia DR3 source ID, matched exactly (case and spaces ignored). ' +
        'It is named if it was detected in this image, or at its estimated position when ' +
        'objects the image doesn’t show are included.',
    ),
    requestError,
    requestList,
  );

  container.replaceChildren(
    h('h3', {}, 'Choose what’s shown'),
    h('p', { class: 'options__label', id: 'opt-types' }, 'Show'),
    typesList,
    typeHint,
    h('p', { class: 'options__label' }, 'Names on the image'),
    h(
      'div',
      { class: 'options__row options__row--check' },
      h('label', { for: 'opt-brightest', class: 'check' }, brightestToggle, h('span', {}, 'Only name the brightest')),
      brightestInput,
    ),
    h(
      'p',
      { class: 'panel__hint' },
      'Brightest by catalogued magnitude (Gaia G for stars; V or B for other objects, which ' +
        'come last when they have none). Stars without a name get their catalogue ID. Only ' +
        'what the image shows is named, unless you include the rest below.',
    ),
    h(
      'label',
      { for: 'opt-undetected', class: 'check check--top' },
      undetectedToggle,
      h('span', {}, 'Also name objects the image doesn’t show (estimated position)'),
    ),
    h(
      'p',
      { class: 'panel__hint' },
      'Objects that aren’t visible or can’t be confirmed are drawn dashed where the plate ' +
        'solution, fitted to the matched stars, places them. The dashed circle shows how ' +
        'accurate that position is.',
    ),
    form,
    resetButton,
  );

  function emit() {
    onChange({ ...options, labels: [...options.labels], types: options.types && [...options.types] });
  }

  function renderTypes() {
    const shown = (key) => options.types === null || options.types.includes(key);
    typesList.replaceChildren(
      ...available.map((t) => {
        const box = h('input', { type: 'checkbox', value: t.key });
        box.checked = shown(t.key);
        box.addEventListener('change', () => {
          const current = options.types === null ? available.map((a) => a.key) : [...options.types];
          const next = box.checked ? [...new Set([...current, t.key])] : current.filter((k) => k !== t.key);
          // Every available type ticked is the same as "all types" (also any added later).
          const all = available.every((a) => next.includes(a.key));
          options = { ...options, types: all ? null : next };
          renderTypes();
          emit();
        });
        return h(
          'label',
          { class: 'check check--type' },
          box,
          h('span', { class: 'check__swatch', style: `--c: ${t.color}` }),
          h('span', {}, t.label),
          h('span', { class: 'check__count' }, String(t.count)),
        );
      }),
    );
    const hidden = available.filter((t) => !shown(t.key)).length;
    typeHint.textContent = hidden
      ? 'Objects you ask for by name below are shown even when their type is hidden.'
      : '';
    typeHint.hidden = !hidden;
  }

  function renderBrightest() {
    brightestToggle.checked = Boolean(options.brightest);
    brightestInput.disabled = !options.brightest;
    // Never overwrite a number that is being typed (a result may arrive meanwhile).
    if (options.brightest && document.activeElement !== brightestInput) {
      brightestInput.value = String(options.brightest);
    }
  }

  function renderRequests() {
    const byQuery = new Map(answers.map((a) => [a.query.toLowerCase(), a]));
    requestList.replaceChildren(
      ...options.labels.map((query) => {
        const answer = byQuery.get(query.toLowerCase());
        const status = answer?.status || 'pending';
        const remove = h(
          'button',
          { type: 'button', class: 'requests__remove', 'aria-label': `Remove ${query}` },
          '×',
        );
        remove.addEventListener('click', () => {
          options = { ...options, labels: options.labels.filter((q) => q !== query) };
          renderRequests();
          emit();
        });
        const title =
          answer?.name && answer.name.toLowerCase() !== query.toLowerCase() ? ` → ${answer.name}` : '';
        return h(
          'li',
          { class: `requests__item requests__item--${status}` },
          h(
            'div',
            {},
            h('p', { class: 'requests__name' }, query, title ? h('span', {}, title) : null),
            h('p', { class: 'requests__status' }, statusText(answer)),
          ),
          remove,
        );
      }),
    );
  }

  function render() {
    renderTypes();
    renderBrightest();
    undetectedToggle.checked = Boolean(options.undetected);
    renderRequests();
    resetButton.hidden = isDefault(options);
  }

  undetectedToggle.addEventListener('change', () => {
    options = { ...options, undetected: undetectedToggle.checked };
    render();
    emit();
  });
  brightestToggle.addEventListener('change', () => {
    const n = Number.parseInt(brightestInput.value, 10);
    options = {
      ...options,
      brightest: brightestToggle.checked ? (n >= 1 ? Math.min(n, MAX_BRIGHTEST) : DEFAULT_BRIGHTEST) : null,
    };
    render();
    emit();
  });
  brightestInput.addEventListener('input', () => {
    const n = Number.parseInt(brightestInput.value, 10);
    if (!(n >= 1 && n <= MAX_BRIGHTEST)) return; // wait for a valid number
    clearTimeout(numberTimer);
    numberTimer = setTimeout(() => {
      options = { ...options, brightest: n };
      emit();
    }, 300);
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = requestInput.value.split(/\s+/).join(' ').trim();
    requestError.hidden = true;
    if (!text) return;
    if (options.labels.some((q) => q.toLowerCase() === text.toLowerCase())) {
      requestInput.value = '';
      return;
    }
    if (options.labels.length >= MAX_LABELS) {
      requestError.textContent = `At most ${MAX_LABELS} objects can be named at once.`;
      requestError.hidden = false;
      return;
    }
    options = { ...options, labels: [...options.labels, text] };
    requestInput.value = '';
    render();
    emit();
  });
  resetButton.addEventListener('click', () => {
    options = { ...DEFAULT_OPTIONS };
    answers = [];
    render();
    emit();
  });

  return {
    /** Show the options for a result (`view` from the server) and the current choices. */
    update(view, current) {
      available = view.types || [];
      answers = view.requests || [];
      options = { ...current };
      // Nothing to choose when the image has only unidentified points (not solved).
      container.hidden = !available.some((t) => t.key !== 'detection');
      render();
    },
    /** The server refused the options (e.g. an old analysis): say so beside them. */
    showError(message) {
      requestError.textContent = message;
      requestError.hidden = !message;
    },
  };
}
