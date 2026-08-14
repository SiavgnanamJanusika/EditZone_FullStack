import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import LandingNavbar from "../../components/navbar/LandingNavbar";
import { OrbitLogo, Input, PrimaryButton, ErrorText } from "../../components/common/UI";
import api from "../../services/api";
import { useAuth } from "../../context/AuthContext";

function verificationError(error, fallback) {
  const data = error.response?.data;
  const detail = data?.detail;
  return data?.message || (typeof detail === "object" ? detail?.message : detail) || fallback;
}

export default function VerifyEmailPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { refreshUser, getPendingVerificationEmail, setPendingVerificationEmail } = useAuth();
  const [email] = useState(() => location.state?.email || getPendingVerificationEmail());
  const [otp, setOtp] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState(location.state?.message || "");
  const [loading, setLoading] = useState(false);
  const [resendAfter, setResendAfter] = useState(0);

  useEffect(() => {
    if (resendAfter <= 0) return undefined;
    const timer = window.setInterval(() => setResendAfter((seconds) => Math.max(0, seconds - 1)), 1000);
    return () => { window.clearInterval(timer); };
  }, [resendAfter]);

  useEffect(() => {
    if (email) setPendingVerificationEmail(email);
  }, [email, setPendingVerificationEmail]);

  const verify = async (event) => {
    event.preventDefault();
    setLoading(true); setError("");
    try {
      const { data } = await api.post("/auth/verify-otp", { email: email.trim().toLowerCase(), otp });
      setPendingVerificationEmail("");
      await refreshUser();
      if (!data.registration_complete) navigate("/complete-profile", { replace: true });
      else if (data.role === "editor") navigate("/editor/dashboard", { replace: true });
      else if (data.role === "admin") navigate("/admin", { replace: true });
      else navigate("/editors", { replace: true });
    } catch (err) {
      setError(verificationError(err, "Verification failed"));
    } finally { setLoading(false); }
  };

  const resend = async () => {
    setError(""); setMessage("");
    try {
      const { data } = await api.post("/auth/send-otp", { email: email.trim().toLowerCase() });
      setMessage(data.message);
      setResendAfter(Number(data.resend_after) || 60);
    } catch (err) {
      setError(verificationError(err, "Unable to resend OTP"));
    }
  };

  return <div className="min-h-screen bg-brand-dark"><LandingNavbar /><section className="max-w-md mx-auto px-6 py-16">
    <div className="auth-orbit mb-6 flex justify-center"><OrbitLogo size={259} logoSize={161} /></div>
    <div className="glass rounded-2xl p-8"><h1 className="font-display text-2xl font-bold text-center">Verify Email</h1>
      <p className="text-gray-400 text-sm text-center my-4">Enter the six-digit code sent to your email. It expires in 5 minutes.</p>
      <form onSubmit={verify} className="space-y-4">
        <Input type="email" value={email} readOnly required />
        <Input inputMode="numeric" value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))} pattern="[0-9]{6}" minLength={6} maxLength={6} placeholder="6-digit OTP" required />
        <ErrorText>{error}</ErrorText>{message && <p className="text-sm text-green-400">{message}</p>}
        <PrimaryButton type="submit" className="w-full" disabled={loading}>{loading ? "Verifying..." : "Verify Email"}</PrimaryButton>
        <button type="button" onClick={resend} disabled={resendAfter > 0} className="w-full text-sm text-brand-gold hover:underline disabled:cursor-not-allowed disabled:text-gray-600">
          {resendAfter > 0 ? `Resend available in ${resendAfter}s` : "Resend verification code"}
        </button>
      </form>
    </div></section></div>;
}
