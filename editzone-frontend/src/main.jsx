import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.jsx";
import { ErrorBoundary } from "./components/common/UX.jsx";
import { GoogleOAuthProvider } from "@react-oauth/google";

// Clear values left by older builds; current authentication uses HttpOnly cookies.
[
  "ez_access_token",
  "ez_refresh_token",
  "ez_admin_sidebar",
  "ez_admin_theme",
].forEach((key) => window.localStorage.removeItem(key));

createRoot(document.getElementById("root")).render(
  <GoogleOAuthProvider clientId={import.meta.env.VITE_GOOGLE_CLIENT_ID || ""} locale="en">
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </GoogleOAuthProvider>
);
