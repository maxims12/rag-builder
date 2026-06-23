"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";

import {
  bootstrapSession,
  login as loginRequest,
  logout as logoutRequest,
  type User,
} from "@/lib/auth";
import { registerSessionExpiredHandler } from "@/lib/api";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const bootstrapped = useRef(false);

  // Bootstrap once: try to re-establish a session from the httpOnly refresh cookie.
  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    let active = true;
    (async () => {
      const restored = await bootstrapSession();
      if (active) {
        setUser(restored);
        setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // When the api layer detects a fully-expired session, drop the user and bounce
  // to /login.
  useEffect(() => {
    registerSessionExpiredHandler(() => {
      setUser(null);
      router.replace("/login");
    });
  }, [router]);

  const login = useCallback(async (email: string, password: string) => {
    const loggedIn = await loginRequest(email, password);
    setUser(loggedIn);
  }, []);

  const logout = useCallback(async () => {
    await logoutRequest();
    setUser(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo<AuthContextValue>(
    () => ({ user, loading, login, logout }),
    [user, loading, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
