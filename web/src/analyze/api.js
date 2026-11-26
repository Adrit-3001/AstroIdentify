/**
 * Talks to the analysis server (`astroidentify serve`). See src/astroidentify/web/server.py.
 */

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

const SERVER_MISSING =
  'Can’t reach the analysis server. Start it from the project folder with “astroidentify serve”, then reload this page.';

async function getJson(url) {
  let response;
  try {
    response = await fetch(url, { cache: 'no-store' });
  } catch {
    throw new ApiError(SERVER_MISSING, 0);
  }
  let body = null;
  try {
    body = await response.json();
  } catch {
    // A plain static server answers /api/... with an HTML 404 page.
    throw new ApiError(SERVER_MISSING, response.status);
  }
  if (!response.ok) throw new ApiError(body?.error || `Request failed (${response.status}).`, response.status);
  return body;
}

/** Formats, size limit, retention and stage names, as the server actually supports them. */
export function fetchConfig() {
  return getJson('/api/config');
}

export function fetchJob(id) {
  return getJson(`/api/jobs/${encodeURIComponent(id)}`);
}

/**
 * Upload the file's bytes as the request body (no multipart form needed).
 * @param {File} file
 * @param {(fraction: number) => void} onProgress
 * @returns {Promise<{id: string, state: string, queue_ahead: number}>}
 */
export function uploadImage(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/jobs');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onerror = () => reject(new ApiError(SERVER_MISSING, 0));
    xhr.onload = () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        return reject(new ApiError(SERVER_MISSING, xhr.status));
      }
      if (xhr.status === 202) resolve(body);
      else reject(new ApiError(body?.error || `Upload failed (${xhr.status}).`, xhr.status));
    };
    xhr.send(file);
  });
}

export const originalUrl = (id) => `/api/jobs/${id}/original.png`;
export const annotatedDownloadUrl = (id) => `/api/jobs/${id}/annotated.png?download=1`;
