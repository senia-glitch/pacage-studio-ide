let revisionListener = null;

export function onRevision(listener) {
  revisionListener = listener;
}

export function studioToken() {
  const meta = document.querySelector('meta[name="studio-token"]');
  return meta ? meta.getAttribute("content") || "" : "";
}

export async function api(path, method = "GET", body, options = {}) {
  const request = {method, headers: {"X-Studio-Token": studioToken()}, ...options};
  if (body !== undefined) {
    request.headers = {...request.headers, "Content-Type": "application/json"};
    request.body = JSON.stringify(body);
  }
  const response = await fetch(path, request);
  const revision = response.headers.get("X-Studio-Revision");
  if (revision && revisionListener) revisionListener(Number(revision) || 0);
  if (!response.ok) {
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    const info = payload && payload.error ? payload.error : {code: `http_${response.status}`, message: `HTTP ${response.status}`};
    const error = new Error(info.message || `HTTP ${response.status}`);
    error.code = info.code;
    error.details = info.details || {};
    error.status = response.status;
    throw error;
  }
  if (response.status === 304) return null;
  return response.json();
}
