import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import LandingNavbar from "../../components/navbar/LandingNavbar";
import { Logo, Input, PrimaryButton, ErrorText } from "../../components/common/UI";
import api from "../../services/api";
import TurnstileChallenge from "../../components/auth/TurnstileChallenge";

export default function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [captchaRequired, setCaptchaRequired] = useState(false);
  const [captchaToken, setCaptchaToken] = useState(null);

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.post("/auth/forgot-password", { email, captcha_token: captchaToken });
      setSent(true);
    } catch (err) {
      if (err.response?.headers?.["x-captcha-required"] === "true") setCaptchaRequired(true);
      setError(err.response?.data?.message || err.response?.data?.detail || "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-brand-dark">
      <LandingNavbar />
      <section className="max-w-md mx-auto px-6 py-16">
        <div className="flex justify-center mb-6"><Logo size={60} /></div>
        <div className="glass rounded-2xl p-8">
          <h1 className="font-display text-2xl font-bold mb-1 text-center">Forgot Password</h1>
          <p className="text-gray-400 text-sm text-center mb-6">We'll send an OTP to reset your password</p>

          {sent ? (
            <div className="text-center">
              <p className="text-green-400 text-sm mb-6">
                If that email exists, a reset OTP has been sent. Check your inbox.
              </p>
              <PrimaryButton className="w-full" onClick={() => navigate("/reset-password", { state: { email } })}>
                Enter OTP
              </PrimaryButton>
            </div>
          ) : (
            <form onSubmit={onSubmit} className="space-y-4">
              <Input type="email" placeholder="Email Address" value={email} onChange={(e) => setEmail(e.target.value)} required />
              {captchaRequired && <TurnstileChallenge onToken={setCaptchaToken} />}
              <ErrorText>{error}</ErrorText>
              <PrimaryButton type="submit" className="w-full" disabled={loading || (captchaRequired && !captchaToken)}>
                {loading ? "Sending..." : "Send Reset OTP"}
              </PrimaryButton>
            </form>
          )}

          <p className="text-sm text-gray-500 text-center mt-6">
            Remembered your password? <Link to="/login" className="text-brand-gold hover:underline">Log in</Link>
          </p>
        </div>
      </section>
    </div>
  );
}
