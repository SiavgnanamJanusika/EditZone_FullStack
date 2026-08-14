import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import api from "../../services/api";
import { toast } from "../../components/common/UX";
import { EmptyState, Loader } from "../../components/common/UI";
import RequestNotificationCard from "../../components/cards/RequestNotificationCard";

const TABS = [
  { id: "pending", label: "New Requests", statuses: ["pending"] },
  { id: "accepted", label: "Accepted", statuses: ["accepted", "payment_failed"] },
  { id: "active", label: "Active Projects", statuses: ["in_progress", "overdue", "admin_review", "revision_requested", "delivered", "cancel_requested", "disputed", "refund_pending"] },
  { id: "closed", label: "Closed", statuses: ["completed", "rejected", "cancelled", "refunded", "expired"] },
];

export default function EditorUpdatesPage() {
  const [requests, setRequests] = useState([]); const [tab, setTab] = useState("pending"); const [loading, setLoading] = useState(true); const [error, setError] = useState("");
  const load = useCallback(async () => { setLoading(true); setError(""); try { const response = await api.get("/requests/mine"); setRequests(response.data.requests || []); } catch (err) { setError(err.response?.data?.message || "Unable to load project updates"); } finally { setLoading(false); } }, []);
  useEffect(() => { load(); }, [load]);
  const respond = async (id, action) => { try { const { data } = await api.patch(`/requests/${id}/respond`, { action }); setRequests((current) => current.map((item) => item.id === id ? { ...item, ...data } : item)); toast(`Project request ${action === "accept" ? "accepted" : "rejected"}`, "success"); return true; } catch (err) { toast(err.response?.data?.message || "Failed to respond", "error"); return false; } };
  const active = TABS.find((item) => item.id === tab); const filtered = useMemo(() => requests.filter((item) => active.statuses.includes(item.status)), [requests, active]);
  return <section className="mx-auto max-w-6xl px-4 py-7 sm:px-6 lg:px-8"><header className="flex items-end justify-between gap-4"><div><p className="text-xs font-bold uppercase tracking-[.2em] text-brand-gold">Editor workspace</p><h1 className="mt-1 text-3xl font-bold">Update</h1><p className="mt-2 text-sm text-slate-400">Manage client requests and follow every project through its real workflow.</p></div><button type="button" onClick={load} aria-label="Refresh project updates" className="rounded-xl border border-white/10 p-3 text-brand-gold hover:bg-white/5"><RefreshCw size={18} /></button></header>
    <div className="mt-7 flex gap-2 overflow-x-auto pb-2">{TABS.map((item) => <button type="button" key={item.id} onClick={() => setTab(item.id)} className={`shrink-0 rounded-full border px-4 py-2 text-sm font-semibold ${tab === item.id ? "border-brand-gold/30 bg-brand-gold/10 text-brand-goldLight" : "border-white/10 text-slate-400"}`}>{item.label} <span className="ml-1 text-xs opacity-70">{requests.filter((request) => item.statuses.includes(request.status)).length}</span></button>)}</div>
    <div className="mt-5">{loading ? <Loader label="Loading project updates…" /> : error ? <div className="glass rounded-2xl p-8 text-center text-red-300">{error}</div> : filtered.length ? <div className="motion-list space-y-4">{filtered.map((request) => <RequestNotificationCard key={request.id} req={request} onRespond={respond} />)}</div> : <EmptyState title={`No ${active.label.toLowerCase()}`} text="Projects will appear here when they enter this workflow stage." />}</div>
  </section>;
}
