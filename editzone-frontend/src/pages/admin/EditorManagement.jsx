import { useCallback, useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Eye, RefreshCw, ShieldCheck, Users, XCircle } from "lucide-react";
import { Loader, Badge } from "../../components/common/UI";
import api from "../../services/api";
import AdminAccountActionModal from "../../components/admin/AdminAccountActionModal";

export default function EditorManagement() {
  const [editors, setEditors] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [notes, setNotes] = useState({});
  const [acting, setActing] = useState("");
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("active");
  const [selected, setSelected] = useState(null);
  const [notice, setNotice] = useState("");

  const load = useCallback(() => {
    setError("");
    setEditors(null);
    Promise.all([
      api.get(`/admin/editors?status=${filter}`),
      api.get("/admin/editors/identity-review"),
    ])
      .then(([editorsResponse, reviewsResponse]) => {
        setEditors(editorsResponse.data.editors || []);
        setReviews(reviewsResponse.data.items || []);
      })
      .catch((err) => { setError(err.response?.data?.message || "Unable to load editors"); setEditors([]); });
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const decide = async (editorId, decision) => {
    setActing(editorId);
    setError("");
    try {
      await api.patch(`/admin/editors/${editorId}/identity-review`, {
        decision,
        note: notes[editorId] || undefined,
      });
      setReviews((current) => current.filter((item) => item.editor_id !== editorId));
    } catch (requestError) {
      setError(requestError.response?.data?.message || "Identity review could not be saved");
    } finally {
      setActing("");
    }
  };

  return (
    <AdminLayout>
      <h1 className="font-display text-2xl font-bold mb-6">Editor Management</h1>
      <div className="mb-5 flex flex-wrap gap-2">{["active", "suspended", "deleted"].map((value) => <button key={value} onClick={() => setFilter(value)} className={`rounded-full px-4 py-2 text-sm capitalize ${filter === value ? "bg-brand-gold text-black" : "bg-white/5 text-gray-300"}`}>{value}</button>)}</div>
      {notice && <p className="mb-4 rounded-xl border border-emerald-400/30 bg-emerald-500/10 p-3 text-sm text-emerald-200">{notice}</p>}
      {reviews.length > 0 && (
        <section className="mb-8">
          <h2 className="mb-4 flex items-center gap-2 font-semibold text-amber-200">
            <ShieldCheck size={20} /> Manual Identity Review ({reviews.length})
          </h2>
          <div className="grid gap-4 lg:grid-cols-2">
            {reviews.map((review) => (
              <article key={review.editor_id} className="glass rounded-xl border border-amber-400/30 p-5">
                <h3 className="font-semibold text-white">{review.username}</h3>
                <p className="text-xs text-gray-400">{review.email} · Masked NIC: {review.nic}</p>
                <ul className="my-3 list-disc space-y-1 pl-5 text-xs text-amber-200">
                  {(review.reasons || []).map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
                <div className="flex flex-wrap gap-2">
                  {[
                    ["NIC front", review.nic_front_review_url],
                  ].map(([label, url]) => url && (
                    <a key={label} href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded border border-brand-border px-2 py-1 text-xs text-brand-gold">
                      <Eye size={14} /> {label}
                    </a>
                  ))}
                </div>
                <p className="mt-2 text-[11px] text-gray-500">Private review links expire in {review.review_urls_expire_seconds || 300} seconds. Access is audit-logged.</p>
                <p className="mt-3 text-xs text-gray-400">
                  OCR confidence: {review.ocr_confidence?.toFixed?.(1) ?? "—"}%
                </p>
                <textarea
                  value={notes[review.editor_id] || ""}
                  onChange={(event) => setNotes((current) => ({ ...current, [review.editor_id]: event.target.value }))}
                  placeholder="Optional review note"
                  maxLength={1000}
                  className="mt-3 w-full rounded-lg border border-brand-border bg-brand-panel p-2 text-sm text-white"
                />
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <button type="button" onClick={() => decide(review.editor_id, "reject")} disabled={acting === review.editor_id} className="inline-flex items-center justify-center gap-2 rounded-lg border border-red-400/50 px-3 py-2 text-sm text-red-300 disabled:opacity-50">
                    <XCircle size={16} /> Reject
                  </button>
                  <button type="button" onClick={() => decide(review.editor_id, "approve")} disabled={acting === review.editor_id} className="inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-500/20 px-3 py-2 text-sm text-emerald-200 disabled:opacity-50">
                    <ShieldCheck size={16} /> Approve
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
      {error ? (
        <div className="glass rounded-2xl p-8 text-center"><p className="text-red-300">{error}</p><button onClick={load} className="mt-4 inline-flex items-center gap-2 text-sm text-brand-gold"><RefreshCw size={15} /> Try again</button></div>
      ) : !editors ? (
        <Loader />
      ) : editors.length === 0 ? (
        <div className="glass rounded-2xl p-10 text-center"><Users className="mx-auto mb-4 text-brand-gold" size={36} /><h2 className="font-semibold text-white">No editors registered yet</h2><p className="mt-2 text-sm text-gray-400">Editor accounts will appear here after registration and profile creation.</p><button onClick={load} className="mt-5 inline-flex items-center gap-2 text-sm text-brand-gold"><RefreshCw size={15} /> Refresh</button></div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {editors.map((e) => (
            <div key={e.id} className="glass rounded-xl p-5">
              <h3 className="font-semibold text-white">{e.username}</h3>
              <p className="text-xs text-brand-gold">{e.category}</p>
              <p className="mt-1 truncate text-xs text-gray-500">{e.email}</p>
              <p className="text-xs text-gray-500">Role: Editor · Registered {e.created_at ? new Date(e.created_at).toLocaleDateString() : "—"}</p>
              <p className="text-xs text-gray-500 mb-2">{e.location || "No location set"}</p>
              <div className="flex items-center justify-between text-sm">
                <Badge tone="gold">Rs. {Number(e.hourly_rate || 0).toLocaleString("en-LK")}/hr</Badge>
                <span className="text-brand-rating text-xs">★ {e.rating_avg} ({e.rating_count})</span>
              </div>
              <p className="text-xs text-gray-500 mt-2">{e.total_views} profile views</p>
              <div className="mt-4 flex items-center justify-between"><Badge tone={e.is_deleted ? "danger" : e.is_banned ? "warning" : "success"}>{e.is_deleted ? "Deleted" : e.is_banned ? "Suspended" : "Active"}</Badge>{e.is_deleted ? <button onClick={() => setSelected({ account: e, action: "restore" })} className="text-xs rounded-lg border border-amber-400/40 px-3 py-1.5 text-amber-200">Restore Account</button> : <button onClick={() => setSelected({ account: e, action: "delete" })} className="text-xs rounded-lg border border-red-400/40 px-3 py-1.5 text-red-300">Delete Account</button>}</div>
            </div>
          ))}
        </div>
      )}
      {selected && <AdminAccountActionModal account={selected.account} action={selected.action} onClose={() => setSelected(null)} onSuccess={(message) => { setSelected(null); setNotice(message); load(); }} />}
    </AdminLayout>
  );
}
