// In-memory access-token store. We deliberately avoid localStorage/sessionStorage
// (CLAUDE.md Rule: no browser storage) so the access token only ever lives in JS
// memory. The refresh token lives in an httpOnly cookie managed by the backend and
// is the durable part of the session — the access token is re-derived from it on load.

let accessToken: string | null = null;

// Subscribers notified whenever the token changes (e.g. so the auth provider can
// reflect login/logout state in React without a global store library).
type Listener = (token: string | null) => void;
const listeners = new Set<Listener>();

export function getAccessTokenFromMemory(): string | null {
  return accessToken;
}

export function setAccessTokenInMemory(token: string | null): void {
  accessToken = token;
  listeners.forEach((cb) => cb(token));
}

export function clearAccessToken(): void {
  setAccessTokenInMemory(null);
}

export function subscribeToken(cb: Listener): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}
