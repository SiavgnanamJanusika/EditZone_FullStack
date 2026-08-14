import { useState } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import { Eye, EyeOff } from "lucide-react";
import LandingNavbar from "../../components/navbar/LandingNavbar";
import { OrbitLogo, Input, PrimaryButton, ErrorText } from "../../components/common/UI";
import { useAuth } from "../../context/AuthContext";
import TurnstileChallenge from "../../components/auth/TurnstileChallenge";
import GoogleLoginButton from "../../components/auth/GoogleLoginButton";

function loginErrorMessage(error) {
  const status = error.response?.status;
  const data = error.response?.data;
  const detail = typeof data?.detail === "object" ? data.detail : null;
  const backendMessage = typeof data === "object" && data
    ? data.message || detail?.message || data.detail
    : "";
  const requestTimedOut = error.code === "ECONNABORTED" || error.code === "ETIMEDOUT";
  const networkUnavailable = !error.response && (requestTimedOut || error.code === "ERR_NETWORK");
  // Vite returns an unstructured 500 when it cannot connect to the proxy
  // target. A real FastAPI 500 uses the structured API error contract.
  const proxyUnavailable = status === 500 && !backendMessage;

  if (networkUnavailable || proxyUnavailable) {
    return "Cannot connect to the server. Please make sure the backend service is running.";
  }
  if (status === 401) return "Invalid email or password.";
  if (status === 429) return "Too many login attempts. Please try again later.";
  if (status === 500) return "Server error. Please try again.";
  if (status === 422) {
    return backendMessage
      || data?.errors?.[0]?.message
      || "Please check your email and password.";
  }
  return backendMessage || "Unable to log in.";
}

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, setPendingVerificationEmail } = useAuth();
  const [form, setForm] = useState({
    email: location.state?.email || "",
    password: "",
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [captchaRequired, setCaptchaRequired] = useState(false);
  const [captchaToken, setCaptchaToken] = useState(null);
  const [captchaResetKey, setCaptchaResetKey] = useState(0);
  const [role, setRole] = useState(location.state?.role === "editor" ? "editor" : "user");
  const queryReturnTo = new URLSearchParams(location.search).get("returnTo");
  const requestedReturnTo = location.state?.from?.pathname
    || (queryReturnTo?.startsWith("/") && !queryReturnTo.startsWith("//") ? queryReturnTo : "");

  const onChange = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await login(
        form.email.trim().toLowerCase(),
        form.password,
        captchaToken,
        role,
      );
      if (!data.registration_complete) {
        navigate("/complete-profile");
      } else if (requestedReturnTo) {
        navigate(requestedReturnTo, { replace: true });
      } else if (data.role === "editor") {
        navigate("/editor/dashboard");
      } else if (data.role === "admin") {
        navigate("/admin");
      } else {
        navigate("/editors", { replace: true });
      }
    } catch (err) {
      const message = loginErrorMessage(err);
      const captchaRequested = err.response?.headers?.["x-captcha-required"] === "true"
        || (err.response?.status === 429 && /captcha verification (?:is required|failed)/i.test(String(message)));
      if (captchaRequested) {
        setCaptchaRequired(true);
        setCaptchaToken(null);
        setCaptchaResetKey((value) => value + 1);
      }
      if (err.response?.status === 403 && message.toLowerCase().includes("verify your email")) {
        const email = err.response?.data?.detail?.email || form.email.trim().toLowerCase();
        setPendingVerificationEmail(email);
        navigate("/verify-email", {
          state: { email, message: "Enter your latest verification code, or request a new one below." },
          replace: true,
        });
        return;
      }
      setError(captchaRequested ? "Please complete the security verification." : message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page min-h-screen bg-brand-dark">
      <LandingNavbar />
      <section className="login-shell mx-auto px-5 py-12 sm:px-6 sm:py-16">
        <div className="login-logo mb-7 flex justify-center"><OrbitLogo size={259} logoSize={161} /></div>
        <div className="login-card auth-glow-card glass p-7 sm:p-9">
          <h1 className="login-title mb-2 text-center font-display text-2xl font-bold">Welcome Back</h1>
          <p className="login-subtitle mb-7 text-center text-sm">Log in to your EditZone account</p>

          <div className="login-role-toggle mb-5 grid grid-cols-2 p-1" aria-label="Account role">
            {[['user', 'Client'], ['editor', 'Editor']].map(([value, label]) => (
              <button key={value} type="button" onClick={() => setRole(value)} className={`login-role-option rounded-lg px-3 py-2 text-sm ${role === value ? 'active font-semibold' : ''}`}>{label}</button>
            ))}
          </div>

          <div className="login-google"><GoogleLoginButton role={role} onError={setError} redirectPath={requestedReturnTo} mode="login" /></div>
          <div className="login-divider my-6 flex items-center gap-3 text-xs uppercase tracking-widest"><span className="h-px flex-1" />or<span className="h-px flex-1" /></div>

          <form onSubmit={onSubmit} className="login-form space-y-4">
            <Input name="email" type="email" placeholder="Email Address" value={form.email} onChange={onChange} required />
            <div className="relative">
              <Input name="password" type={showPassword ? "text" : "password"} placeholder="Password" value={form.password} onChange={onChange} required className="pr-12" />
              <button
                type="button"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                aria-pressed={showPassword}
                className="password-visibility-toggle absolute right-3 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-lg text-gray-400 hover:bg-white/5 hover:text-brand-gold focus-visible:text-brand-gold"
              >
                {showPassword ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
              </button>
            </div>
            <div className="text-right">
              <Link to="/forgot-password" className="login-text-link text-sm">Forgot Password?</Link>
            </div>

            {captchaRequired && <TurnstileChallenge key={captchaResetKey} onToken={setCaptchaToken} onError={setError} />}

            <ErrorText>{error}</ErrorText>

            <PrimaryButton type="submit" className="login-submit w-full" disabled={loading || (captchaRequired && !captchaToken)}>
              {loading ? "Logging in..." : "Login"}
            </PrimaryButton>
          </form>

          <p className="login-register-copy mt-7 text-center text-sm">
            Don't have an account?{" "}
            <Link to="/choose-role" className="login-text-link">Register</Link>
          </p>
        </div>
      </section>
    </div>
  );
}
