import axios from "axios";

const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").trim().replace(/\/$/, "");
// VITE_API_BASE_URL is intentionally the backend origin in deployment docs.
// Keep accepting the legacy value that already includes /api/v1.
const API_BASE_URL = configuredBaseUrl
  ? `${configuredBaseUrl}${/\/api\/v1$/.test(configuredBaseUrl) ? "" : "/api/v1"}`
  : "/api/v1";

const api = axios.create({ baseURL: API_BASE_URL, withCredentials: true });

let refreshPromise = null;
let bearerAccessToken = null;

// EditZone uses Secure/HttpOnly cookies in the browser. This opt-in bearer
// slot supports native/API clients and deployments that explicitly return an
// access token without persisting authentication data in browser storage.
export function setBearerAccessToken(token) {
  bearerAccessToken = typeof token === "string" && token.trim() ? token.trim() : null;
}

api.interceptors.request.use((config) => {
  if (bearerAccessToken && !config.headers?.Authorization) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${bearerAccessToken}`;
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    const original = err.config;
    const endpoint = String(original?.url || "").split("?", 1)[0];
    const isAuthEndpoint = ["/auth/login", "/auth/google", "/auth/refresh", "/auth/register", "/auth/verify-otp", "/auth/session"].includes(endpoint);
    if (err.response?.status !== 401 || !original || original._refreshRetried || isAuthEndpoint) {
      return Promise.reject(err);
    }
    original._refreshRetried = true;
    try {
      if (!refreshPromise) {
        refreshPromise = api.post("/auth/refresh").finally(() => {
          refreshPromise = null;
        });
      }
      await refreshPromise;
      return api(original);
    } catch (refreshError) {
      if (!window.location.pathname.includes("/login")) {
        const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        window.location.assign(`/login?returnTo=${encodeURIComponent(returnTo)}`);
      }
      return Promise.reject(refreshError);
    }
  }
);

export default api;
export { API_BASE_URL };
