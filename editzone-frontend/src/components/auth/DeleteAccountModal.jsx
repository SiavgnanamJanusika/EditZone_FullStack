import { GoogleLogin } from "@react-oauth/google";
import { AlertTriangle, Trash2, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { toast } from "../common/UX";

export default function DeleteAccountModal({ open, onClose }) {
  const navigate = useNavigate();
  const { user, deleteAccount } = useAuth();
  const [confirmation, setConfirmation] = useState("");
  const [password, setPassword] = useState("");
  const [reason, setReason] = useState("");
  const [googleCredential, setGoogleCredential] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const needsPassword = Boolean(user?.has_password);
  const ready = confirmation === "DELETE" && (needsPassword ? Boolean(password) : Boolean(googleCredential));

  if (!open) return null;

  const close = () => {
    if (busy) return;
    setConfirmation(""); setPassword(""); setReason(""); setGoogleCredential(""); setError("");
    onClose();
  };

  const remove = async () => {
    if (!ready) return;
    setBusy(true); setError("");
    try {
      const data = await deleteAccount({
        confirmation,
        current_password: needsPassword ? password : undefined,
        google_credential: needsPassword ? undefined : googleCredential,
        reason: reason.trim() || undefined,
      });
      toast(data.message || "Your account has been deleted successfully.", "success");
      navigate("/", { replace: true });
    } catch (err) {
      const response = err.response?.data;
      const detail = response?.detail;
      const message = response?.message || detail?.message;
      const blockers = response?.blockers || detail?.blockers;
      if (blockers?.length) setError(blockers.join(". "));
      else if (typeof message === "string" && message.trim()) setError(message);
      else if (typeof detail === "string" && detail.trim()) setError(detail);
      else if (!err.response) setError("Could not reach the account service. Check your connection and try again.");
      else setError("The server could not complete account deletion. Please try again later.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[110] grid place-items-center bg-black/75 p-4 backdrop-blur-sm" onMouseDown={(event) => event.target === event.currentTarget && close()}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="delete-account-title" className="glass max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div className="grid h-11 w-11 place-items-center rounded-xl bg-red-500/10 text-red-300"><AlertTriangle size={22} /></div>
          <button type="button" onClick={close} disabled={busy} aria-label="Close" className="rounded-full p-2 text-gray-400 hover:bg-white/10 hover:text-white"><X size={18} /></button>
        </div>
        <h2 id="delete-account-title" className="mt-4 font-display text-xl font-semibold">Delete Account</h2>
        <p className="mt-2 text-sm leading-6 text-gray-300">Are you sure you want to permanently delete your account?</p>
        <p className="mt-2 text-sm leading-6 text-gray-400">This will remove your profile, NIC verification data and active login sessions.</p>
        <label className="mt-5 block text-sm text-gray-300">Type <strong className="text-white">DELETE</strong> to confirm
          <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" className="mt-2 w-full rounded-lg border border-white/10 bg-brand-panel px-4 py-2.5 text-white focus:border-red-400 focus:outline-none" />
        </label>
        {needsPassword ? (
          <label className="mt-4 block text-sm text-gray-300">Confirm your password
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" className="mt-2 w-full rounded-lg border border-white/10 bg-brand-panel px-4 py-2.5 text-white focus:border-red-400 focus:outline-none" />
          </label>
        ) : (
          <div className="mt-4">
            <p className="mb-2 text-sm text-gray-300">Re-authenticate with the Google account linked to EditZone</p>
            <GoogleLogin onSuccess={({ credential }) => { setGoogleCredential(credential || ""); setError(""); }} onError={() => setError("Google re-authentication failed")} text="signin_with" locale="en" theme="filled_black" shape="pill" />
            {googleCredential && <p className="mt-2 text-sm text-green-400">Google identity confirmed.</p>}
          </div>
        )}
        <label className="mt-4 block text-sm text-gray-300">Why are you deleting your account? (Optional)
          <textarea value={reason} onChange={(event) => setReason(event.target.value.slice(0, 500))} rows={3} className="mt-2 w-full resize-none rounded-lg border border-white/10 bg-brand-panel px-4 py-2.5 text-white focus:border-red-400 focus:outline-none" />
        </label>
        {error && <p role="alert" className="mt-4 text-sm text-red-400">{error}</p>}
        <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <button type="button" onClick={close} disabled={busy} className="btn-secondary">Cancel</button>
          <button type="button" onClick={remove} disabled={!ready || busy} className="btn-danger inline-flex items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-40"><Trash2 size={17} />{busy ? "Deleting account…" : "Permanently Delete Account"}</button>
        </div>
      </div>
    </div>
  );
}
