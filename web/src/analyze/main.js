/**
 * The upload workspace: choose a file -> upload -> follow the analysis -> view and download.
 *
 * The current job id and display options live in the address
 * (#job=<id>&types=...&brightest=...&label=...), so reloading the page or coming back later
 * resumes the same analysis with the same choices (until it expires on the server).
 */
import { annotatedDownloadUrl, fetchConfig, fetchJob, fetchView, originalUrl, uploadImage } from './api.js';
import { createOptions, DEFAULT_OPTIONS, isDefault, optionsFromParams, optionsToParams } from './options.js';
import { renderPanel } from './panel.js';
import { createViewer } from './viewer.js';

const $ = (id) => document.getElementById(id);
const els = {
  views: {
    upload: $('view-upload'),
    progress: $('view-progress'),
    result: $('view-result'),
    failed: $('view-failed'),
  },
  dropzone: $('dropzone'),
  input: $('file-input'),
  empty: $('drop-empty'),
  selected: $('drop-selected'),
  chooseButton: $('choose-file'),
  analyzeButton: $('analyze'),
  replaceButton: $('replace'),
  preview: $('file-preview'),
  placeholder: $('file-placeholder'),
  fileName: $('file-name'),
  fileSize: $('file-size'),
  formats: $('formats'),
  uploadError: $('upload-error'),
  uploadNote: $('upload-note'),
  progressTitle: $('progress-title'),
  progressFile: $('progress-file'),
  progressElapsed: $('progress-elapsed'),
  progressImage: $('progress-image'),
  progressPlaceholder: $('progress-placeholder'),
  progressFigure: $('progress-figure'),
  stages: $('stages'),
  download: $('download'),
  panelOutcome: $('panel-outcome'),
  panelDetails: $('panel-details'),
  failedMessage: $('failed-message'),
};

const viewer = createViewer({
  frame: $('viewer-frame'),
  image: $('viewer-image'),
  markersSvg: $('viewer-markers'),
  labelsLayer: $('viewer-labels'),
});

const options = createOptions($('view-options'), (next) => applyOptions(next));

const POLL_MS = 1500;
const state = {
  job: null,
  options: { ...DEFAULT_OPTIONS },
  viewRequest: 0,
  config: null,
  file: null,
  previewUrl: null,
  jobId: null,
  pollTimer: null,
  tickTimer: null,
  elapsed: null,
  elapsedAt: 0,
};

// ------------------------------------------------------------------------------- helpers

function showView(name) {
  for (const [key, el] of Object.entries(els.views)) el.hidden = key !== name;
  window.scrollTo({ top: 0 });
}

const MB = 1024 * 1024; // the server's limit is in binary megabytes too

function formatBytes(n) {
  if (n >= MB) return `${(n / MB).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} bytes`;
}

function formatElapsed(s) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return m ? `${m}:${String(sec).padStart(2, '0')} elapsed` : `${sec} s elapsed`;
}

function extensionOf(name) {
  const lower = name.toLowerCase();
  const all = (state.config?.formats || []).flatMap((f) => f.extensions);
  return all.sort((a, b) => b.length - a.length).find((ext) => lower.endsWith(ext)) || null;
}

const isRaster = (ext) => ['.png', '.jpg', '.jpeg'].includes(ext);

function setUploadError(message) {
  els.uploadError.textContent = message || '';
  els.uploadError.hidden = !message;
}

function setJobInAddress(id, viewOptions = DEFAULT_OPTIONS) {
  const url = new URL(window.location.href);
  url.hash = id ? optionsToParams(viewOptions, new URLSearchParams({ job: id })).toString() : '';
  history.replaceState(null, '', id ? url : url.pathname);
}

function stopTimers() {
  clearTimeout(state.pollTimer);
  clearInterval(state.tickTimer);
  state.pollTimer = state.tickTimer = null;
}

// ------------------------------------------------------------------------------- upload

function describeLimits(config) {
  const names = config.formats.map((f) => f.name);
  const list = `${names.slice(0, -1).join(', ')} or ${names.at(-1)}`;
  const exts = config.formats.flatMap((f) => f.extensions).join(' ');
  els.formats.textContent = `${list} (${exts}) · up to ${Math.round(config.max_upload_bytes / MB)} MB`;
  els.uploadNote.textContent =
    'Analysis runs on this computer with Astrometry.net, then looks stars and objects up in ' +
    'Gaia DR3 and SIMBAD. Plate solving can take a few minutes. Uploads and results are deleted ' +
    `${config.retention_hours} hours after the analysis finishes.`;
}

function selectFile(file) {
  setUploadError('');
  if (!file) return;
  if (!state.config) {
    setUploadError('The analysis server isn’t available yet, so the file can’t be checked or analyzed.');
    return;
  }
  const ext = extensionOf(file.name);
  if (!ext) {
    setUploadError(`“${file.name}” isn’t a supported image. Choose a JPEG, PNG or FITS file.`);
    return;
  }
  if (file.size === 0) {
    setUploadError(`“${file.name}” is empty.`);
    return;
  }
  if (file.size > state.config.max_upload_bytes) {
    setUploadError(
      `“${file.name}” is ${formatBytes(file.size)}; the limit is ${Math.round(state.config.max_upload_bytes / MB)} MB.`,
    );
    return;
  }
  state.file = file;
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = isRaster(ext) ? URL.createObjectURL(file) : null;
  els.preview.hidden = !state.previewUrl;
  els.placeholder.hidden = Boolean(state.previewUrl);
  if (state.previewUrl) els.preview.src = state.previewUrl;
  else els.preview.removeAttribute('src');
  els.preview.alt = `Preview of ${file.name}`;
  els.fileName.textContent = file.name;
  els.fileSize.textContent = formatBytes(file.size);
  els.empty.hidden = true;
  els.selected.hidden = false;
  els.dropzone.classList.add('has-file');
  els.analyzeButton.focus();
}

function wireUpload() {
  els.chooseButton.addEventListener('click', () => els.input.click());
  els.replaceButton.addEventListener('click', () => els.input.click());
  els.input.addEventListener('change', () => {
    selectFile(els.input.files[0]);
    els.input.value = '';
  });
  // The empty drop zone is clickable as a whole.
  els.empty.addEventListener('click', (e) => {
    if (e.target === els.empty || !e.target.closest('button')) els.input.click();
  });
  let depth = 0;
  els.dropzone.addEventListener('dragenter', (e) => {
    e.preventDefault();
    depth++;
    els.dropzone.classList.add('is-dragging');
  });
  els.dropzone.addEventListener('dragover', (e) => e.preventDefault());
  els.dropzone.addEventListener('dragleave', () => {
    if (--depth <= 0) els.dropzone.classList.remove('is-dragging');
  });
  els.dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    els.dropzone.classList.remove('is-dragging');
    const files = e.dataTransfer?.files;
    if (files?.length > 1) setUploadError('Drop one image at a time.');
    else selectFile(files?.[0]);
  });
  // Don't let a file dropped beside the zone open in the tab instead.
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => e.preventDefault());
  els.analyzeButton.addEventListener('click', startAnalysis);
}

async function startAnalysis() {
  if (!state.file) return;
  const file = state.file;
  showProgress({ filename: file.name });
  renderStages(null, `Uploading… 0%`);
  els.analyzeButton.disabled = true;
  try {
    const job = await uploadImage(file, (f) => renderStages(null, `Uploading… ${Math.round(f * 100)}%`));
    state.jobId = job.id;
    setJobInAddress(job.id);
    poll();
  } catch (error) {
    showView('upload');
    setUploadError(error.message);
  } finally {
    els.analyzeButton.disabled = false;
  }
}

// ------------------------------------------------------------------------------- progress

function showProgress(job) {
  showView('progress');
  els.progressFile.textContent = job.filename || '';
  const url = state.previewUrl;
  els.progressImage.hidden = !url;
  els.progressPlaceholder.hidden = Boolean(url);
  if (url) els.progressImage.src = url;
  els.progressImage.alt = job.filename ? `Image being analyzed: ${job.filename}` : '';
}

/** Stage list from the server's real stage names; `uploadText` shows the upload step. */
function renderStages(job, uploadText = null) {
  const stages = state.config?.stages || [];
  const progress = job?.progress || { stage: null, completed: [], failed: null };
  const rows = [];
  rows.push({
    label: uploadText || 'Uploading the image',
    status: uploadText ? 'active' : 'done',
  });
  let afterFailure = false;
  for (const { key, label } of stages) {
    let status = 'pending';
    if (progress.completed.includes(key)) status = 'done';
    else if (progress.failed === key) {
      status = 'failed';
      afterFailure = true;
    } else if (progress.stage === key) status = 'active';
    else if (afterFailure && job?.state === 'done') status = 'skipped';
    rows.push({ label, status });
  }
  els.stages.replaceChildren(
    ...rows.map(({ label, status }) => {
      const li = document.createElement('li');
      li.className = `stage stage--${status}`;
      const text = document.createElement('span');
      text.textContent = label;
      li.append(document.createElement('i'), text);
      if (status === 'active') li.setAttribute('aria-current', 'step');
      const note = { done: 'done', failed: 'didn’t succeed', skipped: 'skipped' }[status];
      if (note) {
        const sr = document.createElement('span');
        sr.className = 'visually-hidden';
        sr.textContent = ` (${note})`;
        li.append(sr);
      }
      return li;
    }),
  );

  if (uploadText) {
    els.progressTitle.textContent = 'Uploading your image…';
  } else if (job?.state === 'queued') {
    const ahead = job.queue_ahead || 0;
    els.progressTitle.textContent =
      ahead > 0 ? `Waiting for ${ahead} other ${ahead === 1 ? 'analysis' : 'analyses'} to finish…` : 'Starting…';
  } else {
    const active = stages.find((s) => s.key === progress.stage);
    els.progressTitle.textContent = active ? `${active.label}…` : 'Analyzing your image…';
  }
}

function tickElapsed() {
  if (state.elapsed === null) {
    els.progressElapsed.textContent = '';
    return;
  }
  const now = state.elapsed + (performance.now() - state.elapsedAt) / 1000;
  els.progressElapsed.textContent = formatElapsed(now);
}

async function poll() {
  clearTimeout(state.pollTimer);
  let job;
  try {
    job = await fetchJob(state.jobId);
  } catch (error) {
    if (error.status === 404) return showFailed(error.message);
    // Temporary connection problem: keep trying, but say so.
    els.progressElapsed.textContent = 'Connection lost — retrying…';
    state.pollTimer = setTimeout(poll, POLL_MS * 2);
    return;
  }
  if (job.state === 'done' && job.result) return showResult(job);
  if (job.state === 'failed' || (job.state === 'done' && !job.result)) {
    return showFailed(job.error || 'The analysis didn’t produce a result.');
  }
  if (els.views.progress.hidden) showProgress(job);
  state.elapsed = job.elapsed_seconds;
  state.elapsedAt = performance.now();
  if (!state.tickTimer) state.tickTimer = setInterval(tickElapsed, 1000);
  tickElapsed();
  renderStages(job);
  state.pollTimer = setTimeout(poll, POLL_MS);
}

// ------------------------------------------------------------------------------- result

async function showResult(job) {
  stopTimers();
  state.job = job;
  showView('result');
  setMode('annotated');
  const stem = (job.filename || 'image').replace(/\.[^.]+(\.gz)?$/i, '');
  els.download.setAttribute('download', `${stem}-annotated.png`);
  // job.result is the default view; other choices (from the address) are fetched.
  state.viewRequest++;
  showViewResult(job.result, DEFAULT_OPTIONS);
  await viewer.show(job.result, originalUrl(job.id), `Analyzed image: ${job.filename}`);
  if (!isDefault(state.options)) applyOptions(state.options);
}

/** Show a result for display options in the panel, options and download link. */
function showViewResult(result, viewOptions) {
  const job = state.job;
  els.download.href = annotatedDownloadUrl(job.id, optionsToParams(viewOptions));
  renderPanel(
    { outcome: els.panelOutcome, details: els.panelDetails },
    result,
    job,
    (id) => viewer.highlight(id),
  );
  if (result.view) options.update(result.view, viewOptions);
}

/** Fetch and show the result for new display options (the latest choice wins). */
async function applyOptions(next) {
  if (!state.job) return;
  state.options = next;
  setJobInAddress(state.job.id, next);
  const request = ++state.viewRequest;
  let result;
  try {
    result = isDefault(next) ? state.job.result : await fetchView(state.job.id, optionsToParams(next));
  } catch (error) {
    if (request === state.viewRequest) options.showError(error.message);
    return;
  }
  if (request !== state.viewRequest) return; // a newer choice was made meanwhile
  showViewResult(result, next);
  viewer.update(result);
}

function setMode(mode) {
  viewer.setMode(mode);
  for (const button of document.querySelectorAll('.segmented button')) {
    button.setAttribute('aria-pressed', String(button.dataset.view === mode));
  }
}

function showFailed(message) {
  stopTimers();
  els.failedMessage.textContent = message;
  showView('failed');
}

function analyzeAnother() {
  stopTimers();
  state.jobId = null;
  state.job = null;
  state.options = { ...DEFAULT_OPTIONS };
  state.file = null;
  state.elapsed = null;
  setJobInAddress(null);
  viewer.reset();
  els.selected.hidden = true;
  els.empty.hidden = false;
  els.dropzone.classList.remove('has-file');
  setUploadError('');
  showView('upload');
  els.chooseButton.focus();
}

// ------------------------------------------------------------------------------- start

async function init() {
  wireUpload();
  for (const button of document.querySelectorAll('.segmented button')) {
    button.addEventListener('click', () => setMode(button.dataset.view));
  }
  for (const button of document.querySelectorAll('[data-action="another"]')) {
    button.addEventListener('click', analyzeAnother);
  }
  try {
    state.config = await fetchConfig();
    describeLimits(state.config);
  } catch (error) {
    setUploadError(error.message);
    els.chooseButton.disabled = true;
  }
  const params = new URLSearchParams(window.location.hash.slice(1));
  const jobId = params.get('job');
  if (jobId && /^[0-9a-f]{32}$/.test(jobId) && state.config) {
    state.jobId = jobId;
    state.options = optionsFromParams(params);
    showProgress({ filename: '' });
    renderStages(null);
    poll();
  }
}

window.__analyze = { state };
init();
