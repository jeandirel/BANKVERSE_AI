/**
 * Same-origin API client. Session cookies are HttpOnly; unsafe requests carry the CSRF double-submit
 * token read from the non-HttpOnly `bv_csrf` cookie. A 401 triggers one silent refresh attempt.
 */

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  requestId?: string;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}, requestId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : undefined;
}

let refreshing: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        setTimeout(() => (refreshing = null), 0);
      });
  }
  return refreshing;
}

type Opts = Omit<RequestInit, "body"> & { body?: unknown; raw?: boolean; noRefresh?: boolean };

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const { body, raw, noRefresh, headers, ...rest } = opts;
  const method = (rest.method ?? "GET").toUpperCase();
  const h = new Headers(headers);
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  if (body !== undefined && !isForm) h.set("content-type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = readCookie("bv_csrf");
    if (csrf) h.set("x-csrf-token", csrf);
  }
  const url = path.startsWith("/api/") ? path : `/api/v1${path}`;
  const doFetch = () =>
    fetch(url, {
      ...rest,
      method,
      headers: h,
      credentials: "same-origin",
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
    });
  let res = await doFetch();
  if (res.status === 401 && !noRefresh && !path.startsWith("/auth/login")) {
    if (await refreshSession()) {
      const csrf = readCookie("bv_csrf");
      if (csrf && h.has("x-csrf-token")) h.set("x-csrf-token", csrf);
      res = await doFetch();
    }
  }
  if (!res.ok) {
    let payload: { error?: { code?: string; message?: string; details?: Record<string, unknown>; request_id?: string } } = {};
    try {
      payload = await res.json();
    } catch {
      /* non-JSON error */
    }
    const e = payload.error ?? {};
    throw new ApiError(res.status, e.code ?? "http_error", e.message ?? res.statusText, e.details ?? {}, e.request_id);
  }
  if (raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const get = <T,>(path: string) => api<T>(path);
export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body ?? {} });
export const patch = <T,>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: "PUT", body });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

export async function download(path: string, fallbackName: string): Promise<void> {
  const res = await api<Response>(path, { raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const m = cd.match(/filename="([^"]+)"/);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = m ? m[1] : fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  }
  const out = s.toString();
  return out ? `?${out}` : "";
}
