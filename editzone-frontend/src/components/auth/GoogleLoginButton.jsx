import { GoogleLogin } from "@react-oauth/google";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";

export default function GoogleLoginButton({ role, onError, redirectPath, mode = "login" }) {
  const navigate = useNavigate();
  const { googleLogin } = useAuth();
  const [loading, setLoading] = useState(false);
  const configured = Boolean(import.meta.env.VITE_GOOGLE_CLIENT_ID?.trim());

  const succeed = async ({ credential }) => {
    if (!credential) {
      onError("Google did not return a login credential. Please try again.");
      return;
    }
    setLoading(true);
    onError("");
    try {
      const data = await googleLogin(credential, role);
      if (!data.registration_complete) navigate("/complete-profile");
      else if (data.role === "editor") navigate("/editor/dashboard");
      else navigate(redirectPath || "/editors", { replace: true });
    } catch (err) {
      onError(err.response?.data?.message || err.response?.data?.detail || "Google login could not be completed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  if (!configured) {
    return <p className="text-center text-sm text-amber-300">Google login is unavailable because the client ID is not configured.</p>;
  }

  return (
    <div className={loading ? "pointer-events-none opacity-60" : ""} aria-busy={loading}>
      <GoogleLogin
        onSuccess={succeed}
        onError={() => onError("The Google popup was closed or login was cancelled.")}
        text={mode === "register" ? "signup_with" : "signin_with"}
        locale="en"
        shape="pill"
        size="large"
        width="320"
        theme="filled_black"
        useOneTap={false}
      />
      {loading && <p className="mt-2 text-center text-sm text-gray-400">Signing in with Google…</p>}
    </div>
  );
}
