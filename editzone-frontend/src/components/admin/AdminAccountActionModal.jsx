import { useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import api from "../../services/api";

export default function AdminAccountActionModal({ account, action = "delete", onClose, onSuccess }) {
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const keyword = action === "restore" ? "RESTORE" : "DELETE";

  const submit = async (event) => {
    event.preventDefault();
    if (submitting || reason.trim().length < 5 || confirmation !== keyword) return;
    setSubmitting(true);
    setError("");
    try {
      const id = account.account_id || account.id;
      const response = action === "restore"
        ? await api.patch(`/admin/accounts/${id}/restore`, { reason: reason.trim(), confirmation })
        : await api.delete(`/admin/accounts/${id}`, { data: { reason: reason.trim(), confirmation } });
      onSuccess(response.data.message);
    } catch (requestError) {
      setError(requestError.response?.data?.detail || requestError.response?.data?.message || "The account could not be updated");
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-black/70 p-4" onMouseDown={(event) => event.target === event.currentTarget && !submitting && onClose()}>
      <form onSubmit={submit} role="alertdialog" aria-modal="true" className="glass w-full max-w-lg rounded-2xl border border-red-400/30 p-6 shadow-2xl">
        <button type="button" onClick={onClose} disabled={submitting} className="float-right rounded-full p-2 text-gray-400 hover:bg-white/10"><X size={18} /></button>
        <AlertTriangle className={action === "restore" ? "text-amber-300" : "text-red-400"} size={34} />
        <h2 className="mt-4 text-xl font-semibold text-white">{action === "restore" ? "Restore Account" : "Delete Account"}</h2>
        <div className="mt-4 rounded-xl bg-white/5 p-4 text-sm"><strong className="block text-white">{account.username || account.name || "Unnamed account"}</strong><span className="text-gray-400">{account.email}</span></div>
        <label className="mt-4 block text-sm text-gray-300">Reason
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} minLength={5} maxLength={1000} required className="mt-2 min-h-24 w-full rounded-xl border border-brand-border bg-brand-panel p-3 text-white" placeholder="Enter the administrative reason" />
        </label>
        <label className="mt-4 block text-sm text-gray-300">Type <strong>{keyword}</strong> to confirm
          <input value={confirmation} onChange={(event) => setConfirmation(event.target.value.toUpperCase())} className="mt-2 w-full rounded-xl border border-brand-border bg-brand-panel p-3 text-white" autoComplete="off" />
        </label>
        {error && <p className="mt-3 text-sm text-red-300">{typeof error === "string" ? error : "The account could not be updated"}</p>}
        <div className="mt-6 flex justify-end gap-3"><button type="button" onClick={onClose} disabled={submitting} className="rounded-lg border border-brand-border px-4 py-2 text-sm">Cancel</button><button type="submit" disabled={submitting || reason.trim().length < 5 || confirmation !== keyword} className={`rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-40 ${action === "restore" ? "bg-amber-600" : "bg-red-600"}`}>{submitting ? "Saving…" : action === "restore" ? "Restore Account" : "Delete Account"}</button></div>
      </form>
    </div>
  );
}
