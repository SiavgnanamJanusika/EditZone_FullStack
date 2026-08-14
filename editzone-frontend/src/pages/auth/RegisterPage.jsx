import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import LandingNavbar from "../../components/navbar/LandingNavbar";
import { OrbitLogo, Input, PrimaryButton, ErrorText } from "../../components/common/UI";
import { useAuth } from "../../context/AuthContext";
import GoogleLoginButton from "../../components/auth/GoogleLoginButton";

export default function RegisterPage() {
  const [params] = useSearchParams();
  const role = params.get("role") === "editor" ? "editor" : "user";
  const navigate = useNavigate();
  const { login, register } = useAuth();

  const [form, setForm] = useState({ username: "", email: "", password: "", nic: "" });
  const [error, setError] = useState("");
  const [existingAccount, setExistingAccount] = useState(false);
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState("register");

  const onChange = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setExistingAccount(false);
    setLoading(true);
    try {
      if (mode === "signin") {
        const data = await login(
          form.email.trim().toLowerCase(),
          form.password,
        );
        if (!data.registration_complete) navigate("/complete-profile");
        else if (data.role === "editor") navigate("/editor/dashboard");
        else if (data.role === "admin") navigate("/admin");
        else navigate("/editors");
      } else {
        const data = await register({
          ...form,
          email: form.email.trim().toLowerCase(),
          nic: form.nic.trim().toUpperCase(),
          role,
        });
        navigate("/verify-email", { state: { email: data.email } });
      }
    } catch (err) {
      const detail = err.response?.data?.detail;
      const message = err.response?.data?.message
        || (typeof detail === "object" ? detail?.message : detail)
        || err.response?.data?.errors?.[0]?.message
        || (mode === "signin" ? "Unable to sign in" : "Registration failed");
      if (mode === "signin" && err.response?.status === 403 && message.toLowerCase().includes("verify your email")) {
        const email = detail?.email || form.email.trim().toLowerCase();
        navigate("/verify-email", {
          state: { email, message: "Enter your latest verification code, or request a new one below." },
          replace: true,
        });
        return;
      }
      setError(message);
      const accountExists = err.response?.status === 409;
      setExistingAccount(accountExists);
      if (accountExists) setMode("signin");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-brand-dark">
      <LandingNavbar />
      <section className="max-w-md mx-auto px-6 py-16">
        <div className="auth-orbit mb-6 flex justify-center"><OrbitLogo size={259} logoSize={161} /></div>
        <div className="auth-glow-card glass rounded-2xl p-8">
          <h1 className="font-display text-2xl font-bold mb-1 text-center">
            {mode === "signin" ? "Sign In" : `Register as ${role === "editor" ? "an Editor" : "a Client"}`}
          </h1>
          <p className="text-gray-400 text-sm text-center mb-6">
            {mode === "signin" ? "Use your existing EditZone account" : "Create your EditZone account"}
          </p>

          <GoogleLoginButton role={role} onError={setError} mode={mode === "signin" ? "login" : "register"} />
          <div className="my-5 flex items-center gap-3 text-xs uppercase tracking-widest text-gray-600"><span className="h-px flex-1 bg-white/10" />or<span className="h-px flex-1 bg-white/10" /></div>

          <form onSubmit={onSubmit} className="space-y-4">
            {mode === "register" && <Input name="username" placeholder="Username" value={form.username} onChange={onChange} required minLength={2} maxLength={50} />}
            <Input name="email" type="email" placeholder="Email Address" value={form.email} onChange={onChange} required />
            <Input name="password" type="password" placeholder="Password (8+ characters, letter and number)" value={form.password} onChange={onChange} required minLength={8} maxLength={128} pattern="(?=.*[A-Za-z])(?=.*\d).{8,128}" title="Use 8–128 characters with at least one letter and one number" />
            {mode === "register" && <Input name="nic" placeholder="NIC Number (e.g. 200012345678 or 991234567V)" value={form.nic} onChange={onChange} required pattern="(?:[0-9]{12}|[0-9]{9}[VvXx])" title="Enter a valid 12-digit or old-format Sri Lankan NIC" />}

            <ErrorText>{error}</ErrorText>

            {existingAccount && (
              <button
                type="button"
                onClick={() => { setMode("signin"); setError(""); }}
                className="block w-full rounded-lg border border-brand-gold/50 bg-brand-gold/10 px-5 py-2.5 text-center text-sm font-semibold text-brand-gold hover:bg-brand-gold/20 transition-colors"
              >
                Account already exists — Sign In here
              </button>
            )}

            <PrimaryButton type="submit" className="w-full" disabled={loading}>
              {loading ? (mode === "signin" ? "Signing in..." : "Creating account...") : (mode === "signin" ? "Sign In" : "Continue")}
            </PrimaryButton>
          </form>

          <p className="text-sm text-gray-500 text-center mt-6">
            {mode === "signin" ? "Need a new account?" : "Already have an account?"}{" "}
            <button
              type="button"
              onClick={() => { setMode(mode === "signin" ? "register" : "signin"); setError(""); setExistingAccount(false); }}
              className="text-brand-gold hover:underline"
            >
              {mode === "signin" ? "Register" : "Sign In here"}
            </button>
          </p>
        </div>
      </section>
    </div>
  );
}
