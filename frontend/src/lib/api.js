/**
 * Client for the threat monitor API.
 *
 * Every call funnels through `request`, which turns a failure into an Error
 * carrying the server's own message. That matters here: the ingest endpoint
 * names the record and field that failed validation, and a generic "request
 * failed" would throw away the one piece of information the user needs.
 */

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, options);
  } catch {
    // fetch only rejects for network-level failures, which here means the
    // backend is not running - by far the most common cause during a demo.
    throw new ApiError(
      `Cannot reach the API at ${BASE}. Is the backend running?`,
      0,
    );
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) {
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
      }
    } catch {
      /* response had no JSON body; the status line will have to do */
    }
    throw new ApiError(detail, response.status);
  }

  return response.json();
}

const qs = (params) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
};

export const getHealth = () => request("/health");

export const getAlerts = ({ minSeverity, limit = 500 } = {}) =>
  request(`/alerts${qs({ min_severity: minSeverity, limit })}`);

export const getWallet = (address, { hops = 2, limit = 300 } = {}) =>
  request(`/wallet/${encodeURIComponent(address)}${qs({ hops, limit })}`);

export const getGraph = ({ minRisk = 0, limit = 600, includeIps = true } = {}) =>
  request(
    `/graph${qs({ min_risk: minRisk, limit, include_ips: includeIps })}`,
  );

export const clearGraph = () => request("/graph", { method: "DELETE" });

export function ingest(file) {
  const body = new FormData();
  body.append("file", file);
  return request("/ingest", { method: "POST", body });
}
