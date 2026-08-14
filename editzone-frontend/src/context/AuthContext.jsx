/* eslint-disable react/only-export-components -- provider and its hook form one public auth API */
import { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import api, { setBearerAccessToken } from "../services/api";
import { unsubscribeBrowserNotifications } from "../services/browserNotifications";

const AuthContext = createContext(null);
const AUTH_REQUEST_TIMEOUT_MS = 15000;
const PENDING_EMAIL_KEY = "editzone:pending-verification-email";

function normalizeUser(user) {
  if (!user) return user;
  const verified = user.is_email_verified ?? user.email_verified ?? false;
  return { ...user, is_email_verified: Boolean(verified), email_verified: Boolean(verified) };
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const authChannelRef = useRef(null);

  const fetchMe = useCallback(async () => {
    try {
      const res = await api.get("/auth/me");
      setUser(normalizeUser(res.data));
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const initializeAuth = async () => {
      try {
        const res = await api.get("/auth/session");
        if (!cancelled && res.data.authenticated) {
          await fetchMe();
          return;
        }
      } catch {
        // Leave the app usable as a guest if the auth service is unavailable.
      }
      if (!cancelled) {
        setUser(null);
        setLoading(false);
      }
    };
    initializeAuth();
    return () => {
      cancelled = true;
    };
  }, [fetchMe]);

  useEffect(() => {
    if (!("BroadcastChannel" in window)) return undefined;
    const channel = new BroadcastChannel("editzone-auth");
    authChannelRef.current = channel;
    channel.onmessage = (event) => {
      if (event.data?.type === "logout" || event.data?.type === "account-deleted") {
        setBearerAccessToken(null);
        setUser(null);
      } else if (event.data?.type === "session-changed") {
        fetchMe();
      }
    };
    return () => { channel.close(); authChannelRef.current = null; };
  }, [fetchMe]);

  const login = async (email, password, captchaToken = null, role = null) => {
    const res = await api.post(
      "/auth/login",
      { email, password, captcha_token: captchaToken, ...(role ? { role } : {}) },
      { timeout: AUTH_REQUEST_TIMEOUT_MS },
    );
    setBearerAccessToken(res.data.access_token);
    // Login already returns the canonical authenticated user. Resolving the
    // submit action must not depend on a second /auth/me round trip, which can
    // otherwise leave the button waiting even after authentication succeeded.
    setUser(normalizeUser(res.data.user));
    authChannelRef.current?.postMessage({ type: "session-changed" });
    return res.data;
  };

  const register = async (payload) => {
    const res = await api.post("/auth/register", payload);
    // Registration intentionally has no authenticated session until the OTP is
    // verified. Calling /auth/me here starts an unnecessary refresh/login
    // redirect that can race with navigation to the verification page.
    setUser(null);
    window.sessionStorage.setItem(PENDING_EMAIL_KEY, res.data.email);
    return res.data;
  };

  const googleLogin = async (credential, role) => {
    const res = await api.post("/auth/google", { credential, role }, { timeout: AUTH_REQUEST_TIMEOUT_MS });
    setBearerAccessToken(res.data.access_token);
    setUser(normalizeUser(res.data.user));
    authChannelRef.current?.postMessage({ type: "session-changed" });
    return res.data;
  };

  const logout = async () => {
    await unsubscribeBrowserNotifications().catch(() => undefined);
    try {
      await api.post("/auth/logout");
    } catch {
      // Clear the local UI session even if the server is temporarily unavailable.
    }
    setUser(null);
    setBearerAccessToken(null);
    authChannelRef.current?.postMessage({ type: "logout" });
    window.location.href = "/";
  };

  const deleteAccount = async (payload) => {
    const res = await api.delete("/account", { data: payload });
    setUser(null);
    setBearerAccessToken(null);
    authChannelRef.current?.postMessage({ type: "account-deleted" });
    // Only chat drafts are persisted by this client. Remove them so account-
    // scoped historical state cannot survive deletion in this browser.
    Object.keys(window.localStorage)
      .filter((key) => key.startsWith("editzone-chat-draft:"))
      .forEach((key) => window.localStorage.removeItem(key));
    window.dispatchEvent(new CustomEvent("editzone:account-deleted"));
    return res.data;
  };

  const getPendingVerificationEmail = () => window.sessionStorage.getItem(PENDING_EMAIL_KEY) || "";
  const setPendingVerificationEmail = (email) => {
    const normalized = String(email || "").trim().toLowerCase();
    if (normalized) window.sessionStorage.setItem(PENDING_EMAIL_KEY, normalized);
    else window.sessionStorage.removeItem(PENDING_EMAIL_KEY);
  };

  return (
    <AuthContext.Provider value={{ user, setUser, loading, login, googleLogin, register, logout, deleteAccount, refreshUser: fetchMe, getPendingVerificationEmail, setPendingVerificationEmail }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
