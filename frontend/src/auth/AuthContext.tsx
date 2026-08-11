import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api, getToken, setToken, UserOut } from "../api";

interface AuthContextValue {
  user: UserOut | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<UserOut>;
  register: (email: string, password: string, displayName: string) => Promise<UserOut>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    api
      .get<UserOut>("/api/v1/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  async function login(email: string, password: string): Promise<UserOut> {
    const res = await api.post<{ access_token: string; user: UserOut }>("/api/v1/auth/login", { email, password });
    setToken(res.access_token);
    setUser(res.user);
    return res.user;
  }

  async function register(email: string, password: string, displayName: string): Promise<UserOut> {
    const res = await api.post<{ access_token: string; user: UserOut }>("/api/v1/auth/register", {
      email,
      password,
      display_name: displayName,
    });
    setToken(res.access_token);
    setUser(res.user);
    return res.user;
  }

  function logout() {
    setToken(null);
    setUser(null);
  }

  return <AuthContext.Provider value={{ user, loading, login, register, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
