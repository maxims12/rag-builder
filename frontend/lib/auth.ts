// Auth helpers: thin wrappers over the API layer that own the access-token
// lifecycle. The refresh token lives in an httpOnly cookie; the access token is
// kept in-memory only (see token-store.ts).

import { apiGet, apiPost, authPath, customFetch } from "@/lib/api";
import {
  clearAccessToken,
  getAccessTokenFromMemory,
  setAccessTokenInMemory,
} from "@/lib/token-store";

export interface User {
  id: number;
  email: string;
  is_active: boolean;
  created_at: string;
}

interface LoginResponse {
  access_token: string;
  token_type: string;
}

interface LogoutResponse {
  success: boolean;
  detail: string;
}

// POST /auth/login -> stores the returned access token in memory.
export async function login(email: string, password: string): Promise<User> {
  const res = await customFetch(authPath("/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
    _retry: false, // login itself must never trigger the refresh interceptor
  });

  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    const message =
      (payload as { detail?: string }).detail || "Invalid email or password";
    throw new Error(message);
  }

  const data = (await res.json()) as LoginResponse;
  setAccessTokenInMemory(data.access_token);
  return getCurrentUser();
}

// GET /auth/me -> current user. Used both after login and on app bootstrap.
export async function getCurrentUser(): Promise<User> {
  return apiGet<User>(authPath("/me"));
}

// POST /auth/logout -> clears the httpOnly cookie server-side + local token.
export async function logout(): Promise<void> {
  try {
    await apiPost<LogoutResponse>(authPath("/logout"));
  } catch {
    // Even if the server call fails, drop the local token so the UI logs out.
  } finally {
    clearAccessToken();
  }
}

// Bootstrap on app load: if there's no in-memory access token, try exchanging the
// httpOnly refresh cookie for one. Returns the user if a valid session exists.
export async function bootstrapSession(): Promise<User | null> {
  if (!getAccessTokenFromMemory()) {
    const refreshed = await refreshAccessToken();
    if (!refreshed) return null;
  }
  try {
    return await getCurrentUser();
  } catch {
    clearAccessToken();
    return null;
  }
}

// POST /auth/refresh -> exchange refresh cookie for a new access token.
async function refreshAccessToken(): Promise<boolean> {
  try {
    const res = await fetch(authPath("/refresh"), {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) return false;
    const data = (await res.json()) as LoginResponse;
    setAccessTokenInMemory(data.access_token);
    return true;
  } catch {
    return false;
  }
}
