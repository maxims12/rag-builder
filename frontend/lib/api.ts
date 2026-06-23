// Central API layer. Every request to the backend goes through here so that:
//   1. the in-memory access token is attached as a Bearer header,
//   2. httpOnly refresh cookies travel with the request (credentials: "include"),
//   3. a 401 with code TOKEN_EXPIRED triggers a single /auth/refresh + retry,
//   4. a failed refresh clears the session and signals a login redirect.
//
// Routing (see next.config.mjs rewrites):
//   - Auth endpoints are proxied at the root: /auth/*  -> backend /auth/*
//   - All other endpoints are proxied under: /api/*    -> backend /*
// Both are same-origin from the browser so the refresh cookie (Path=/auth/refresh)
// is sent automatically on the refresh call.

import {
  getAccessTokenFromMemory,
  setAccessTokenInMemory,
  clearAccessToken,
} from "@/lib/token-store";

const AUTH_PREFIX = "/auth";
const API_PREFIX = "/api";

// ---------------------------------------------------------------------------
// Error contract (CONTRACT.md §5)
// ---------------------------------------------------------------------------

export interface ApiErrorPayload {
  detail?: string;
  code?: string;
  timestamp?: string;
}

export class ApiError extends Error {
  status: number;
  code?: string;
  detail?: string;

  constructor(status: number, payload: ApiErrorPayload | null) {
    super(payload?.detail || `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = payload?.code;
    this.detail = payload?.detail;
  }
}

// ---------------------------------------------------------------------------
// Session-expiry signalling
// ---------------------------------------------------------------------------

// The auth provider registers a handler so a fully-expired session can force a
// redirect to /login without api.ts importing Next router internals.
type SessionExpiredHandler = () => void;
let onSessionExpired: SessionExpiredHandler | null = null;

export function registerSessionExpiredHandler(cb: SessionExpiredHandler): void {
  onSessionExpired = cb;
}

function clearSessionAndRedirectToLogin(): void {
  clearAccessToken();
  if (onSessionExpired) {
    onSessionExpired();
  } else if (typeof window !== "undefined") {
    window.location.href = "/login";
  }
}

// ---------------------------------------------------------------------------
// Refresh coordination (CONTRACT.md §5 interceptor)
// ---------------------------------------------------------------------------

let isRefreshing = false;
let refreshSubscribers: ((token: string | null) => void)[] = [];

function subscribeTokenRefresh(cb: (token: string | null) => void) {
  refreshSubscribers.push(cb);
}

function onRefreshed(token: string | null) {
  refreshSubscribers.forEach((cb) => cb(token));
  refreshSubscribers = [];
}

async function doRefresh(): Promise<string | null> {
  try {
    const res = await fetch(`${AUTH_PREFIX}/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { access_token?: string };
    return data.access_token ?? null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

function buildHeaders(
  base: HeadersInit | undefined,
  token: string | null,
  hasJsonBody: boolean
): Headers {
  const headers = new Headers(base);
  if (hasJsonBody && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return headers;
}

export interface CustomFetchOptions extends RequestInit {
  // When true (default) a TOKEN_EXPIRED 401 triggers an automatic refresh+retry.
  _retry?: boolean;
}

export async function customFetch(
  url: string,
  options: CustomFetchOptions = {}
): Promise<Response> {
  const token = getAccessTokenFromMemory();
  // Only set Content-Type: application/json when we actually send a string body
  // (our helpers JSON.stringify before calling). Avoids forcing the header on
  // bodyless GET/POST requests.
  const hasJsonBody = typeof options.body === "string";

  const requestInit: RequestInit = {
    ...options,
    headers: buildHeaders(options.headers, token, hasJsonBody),
    credentials: "include",
  };

  const response = await fetch(url, requestInit);

  if (response.status !== 401 || options._retry === false) {
    return response;
  }

  // Inspect the error code without consuming the original body.
  const errorData: ApiErrorPayload = await response
    .clone()
    .json()
    .catch(() => ({}));

  if (errorData.code !== "TOKEN_EXPIRED") {
    return response;
  }

  // Token expired: coordinate a single refresh and replay this request.
  if (!isRefreshing) {
    isRefreshing = true;
    const newToken = await doRefresh();
    isRefreshing = false;

    if (newToken) {
      setAccessTokenInMemory(newToken);
      onRefreshed(newToken);
    } else {
      onRefreshed(null);
      clearSessionAndRedirectToLogin();
      return response;
    }
  }

  return new Promise<Response>((resolve) => {
    subscribeTokenRefresh((newToken) => {
      if (!newToken) {
        resolve(response);
        return;
      }
      const retryInit: RequestInit = {
        ...options,
        headers: buildHeaders(options.headers, newToken, hasJsonBody),
        credentials: "include",
      };
      resolve(fetch(url, retryInit));
    });
  });
}

// ---------------------------------------------------------------------------
// Typed JSON helpers
// ---------------------------------------------------------------------------

async function parseOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const payload: ApiErrorPayload | null = await res
      .json()
      .catch(() => null);
    throw new ApiError(res.status, payload);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

function resolvePath(path: string): string {
  // Absolute URLs pass through untouched (rare). Auth paths use the /auth prefix
  // proxy; everything else is namespaced under /api so the backend root endpoints
  // resolve via next.config rewrites.
  if (/^https?:\/\//.test(path)) return path;
  if (path.startsWith(AUTH_PREFIX)) return path;
  if (path.startsWith(API_PREFIX)) return path;
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${API_PREFIX}${normalized}`;
}

export async function apiGet<T>(
  path: string,
  options: CustomFetchOptions = {}
): Promise<T> {
  const res = await customFetch(resolvePath(path), { ...options, method: "GET" });
  return parseOrThrow<T>(res);
}

export async function apiPost<T>(
  path: string,
  body?: unknown,
  options: CustomFetchOptions = {}
): Promise<T> {
  const res = await customFetch(resolvePath(path), {
    ...options,
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return parseOrThrow<T>(res);
}

export async function apiPut<T>(
  path: string,
  body?: unknown,
  options: CustomFetchOptions = {}
): Promise<T> {
  const res = await customFetch(resolvePath(path), {
    ...options,
    method: "PUT",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return parseOrThrow<T>(res);
}

export async function apiDelete<T>(
  path: string,
  options: CustomFetchOptions = {}
): Promise<T> {
  const res = await customFetch(resolvePath(path), {
    ...options,
    method: "DELETE",
  });
  return parseOrThrow<T>(res);
}

// Expose resolved paths for SSE consumers (EventSource can't use customFetch).
export function authPath(path: string): string {
  return path.startsWith("/") ? `${AUTH_PREFIX}${path}` : `${AUTH_PREFIX}/${path}`;
}

export function apiUrl(path: string): string {
  return resolvePath(path);
}
