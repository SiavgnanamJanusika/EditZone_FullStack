import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import LandingNavbar from "../../components/navbar/LandingNavbar";
import { Logo, Input, PrimaryButton, ErrorText } from "../../components/common/UI";
import { useAuth } from "../../context/AuthContext";
import api from "../../services/api";
import EditorIdentityVerification from "../../components/auth/EditorIdentityVerification";
import DeleteAccountModal from "../../components/auth/DeleteAccountModal";

const DISTRICTS = [
  "Colombo", "Gampaha", "Kalutara", "Kandy", "Matale", "Nuwara Eliya", "Galle", "Matara",
  "Hambantota", "Jaffna", "Kilinochchi", "Mannar", "Vavuniya", "Mullaitivu", "Batticaloa",
  "Ampara", "Trincomalee", "Kurunegala", "Puttalam", "Anuradhapura", "Polonnaruwa",
  "Badulla", "Monaragala", "Ratnapura", "Kegalle",
];

export default function CompleteProfilePage() {
  const navigate = useNavigate();
  const { user, refreshUser } = useAuth();
  const [form, setForm] = useState({
    username: user?.username || "",
    nic: user?.nic || "",
    district: "",
    gender: "Male",
    phone: "",
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [nicVerified, setNicVerified] = useState(user?.role !== "editor");

  useEffect(() => {
    if (user) {
      setForm((current) => ({
        ...current,
        username: current.username || user.username || "",
        nic: current.nic || user.nic || "",
      }));
    }
  }, [user]);

  const onChange = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (user?.role === "editor" && !nicVerified) {
      setError("Complete NIC and live selfie verification before finishing registration");
      return;
    }
    setLoading(true);
    try {
      const res = await api.post("/auth/complete-profile", form);
      await refreshUser();
      navigate(res.data.redirect_to === "editor-dashboard" ? "/editor/dashboard" : "/editors");
    } catch (err) {
      setError(err.response?.data?.message || err.response?.data?.errors?.[0]?.message || "Failed to save profile");
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
          <h1 className="font-display text-2xl font-bold mb-1 text-center">Complete Your Profile</h1>
          <p className="text-gray-400 text-sm text-center mb-6">Just a few more details to get started</p>

          <form onSubmit={onSubmit} className="space-y-4">
            <Input name="username" placeholder="Username" value={form.username} onChange={onChange} required minLength={2} maxLength={50} />
            <Input name="nic" placeholder="NIC Number" value={form.nic} onChange={onChange} readOnly={user?.role === "editor" && Boolean(user?.nic)} required pattern="(?:[0-9]{12}|[0-9]{9}[VvXx])" title="Enter a valid Sri Lankan NIC" />
            <Input name="phone" type="tel" placeholder="Phone Number (e.g. 0771234567)" value={form.phone} onChange={onChange} required pattern="(?:\+94|0)[0-9]{9}" title="Enter a valid Sri Lankan phone number" />

            <select
              name="district"
              value={form.district}
              onChange={onChange}
              required
              className="w-full px-4 py-2.5 rounded-lg bg-brand-panel border border-brand-border text-white focus:outline-none focus:border-brand-goldLight"
            >
              <option value="">Select District / Place</option>
              {DISTRICTS.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>

            <div className="flex gap-4">
              {["Male", "Female"].map((g) => (
                <label key={g} className="flex items-center gap-2 text-sm text-gray-300">
                  <input type="radio" name="gender" value={g} checked={form.gender === g} onChange={onChange} className="accent-brand-gold" />
                  {g}
                </label>
              ))}
            </div>

            {user?.role === "editor" && (
              <EditorIdentityVerification
                nic={form.nic}
                onVerified={setNicVerified}
              />
            )}

            <ErrorText>{error}</ErrorText>

            <PrimaryButton
              type="submit"
              className="w-full"
              disabled={loading || (user?.role === "editor" && !nicVerified)}
            >
              {loading ? "Saving..." : "Finish Registration"}
            </PrimaryButton>
          </form>

          <section className="mt-8 border-t border-red-400/20 pt-6" aria-labelledby="danger-zone-title">
            <h2 id="danger-zone-title" className="font-display text-lg font-semibold text-red-300">Danger Zone</h2>
            <p className="mt-2 text-sm leading-6 text-gray-400">Delete your account and remove your personal profile information.<br />This action cannot be undone.</p>
            <button type="button" onClick={() => setDeleteOpen(true)} className="mt-4 w-full rounded-lg border border-red-400/60 bg-red-500/5 px-4 py-2.5 text-sm font-semibold text-red-300 transition hover:border-red-300 hover:bg-red-500/10 focus:outline-none focus:ring-2 focus:ring-red-400/40">
              Delete Account
            </button>
          </section>
        </div>
      </section>
      <DeleteAccountModal open={deleteOpen} onClose={() => setDeleteOpen(false)} />
    </div>
  );
}
