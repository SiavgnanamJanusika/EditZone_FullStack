import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import AdminLayout from "../../components/admin/AdminLayout";
import { Loader, Badge, PrimaryButton, OutlineButton } from "../../components/common/UI";
import api from "../../services/api";
import { FolderKanban, RefreshCw } from "lucide-react";
import { ConfirmModal, toast } from "../../components/common/UX";
import { protectedMediaUrl } from "../../services/media";

const STATUS_TONE = {
  pending: "warning", accepted: "gold", rejected: "danger",
  in_progress: "gold", delivered: "warning", completed: "success",
  revision_requested: "warning", cancel_requested: "warning", cancelled: "danger",
  disputed: "danger", refund_pending: "warning", refunded: "success", overdue: "danger",
  expired: "default", admin_review: "gold", payment_failed: "danger",
};
const FILTERS = [
  { id: "all", label: "All Projects", statuses: null },
  { id: "pending", label: "New Requests", statuses: ["pending"] },
  { id: "accepted", label: "Accepted", statuses: ["accepted"] },
  { id: "active", label: "Active Projects", statuses: ["in_progress", "overdue", "admin_review", "revision_requested", "delivered"] },
  { id: "attention", label: "Needs Attention", statuses: ["cancel_requested", "disputed", "refund_pending", "payment_failed"] },
  { id: "completed", label: "Closed", statuses: ["completed", "cancelled", "refunded", "expired", "rejected"] },
];

export default function ProjectMonitoring() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  const [confirmation, setConfirmation] = useState(null);
  const selectedFilter = FILTERS.some((item) => item.id === searchParams.get("status")) ? searchParams.get("status") : "all";
  const activeFilter = FILTERS.find((item) => item.id === selectedFilter);
  const visibleProjects = projects?.filter((project) => !activeFilter.statuses || activeFilter.statuses.includes(project.status));

  const load = () => {
    setError("");
    setProjects(null);
    return api.get("/admin/projects")
      .then((res) => setProjects(res.data.projects || []))
      .catch((err) => { setError(err.response?.data?.message || "Unable to load projects"); setProjects([]); });
  };
  useEffect(() => { load(); }, []);

  const verify = async (id, approve) => {
    setBusyId(id);
    try {
      await api.patch(`/admin/projects/${id}/verify-delivery`, { approve });
      toast(approve ? "Delivery approved" : "Revision requested", "success");
      setConfirmation(null);
      load();
    } catch (err) {
      toast(err.response?.data?.message || "Action failed", "error");
    } finally {
      setBusyId(null);
    }
  };

  const preview = async (fileUrl) => {
    try {
      window.open(await protectedMediaUrl(fileUrl), "_blank", "noopener,noreferrer");
    } catch (err) {
      toast(err.response?.data?.message || "Protected preview is unavailable", "error");
    }
  };

  return (
    <AdminLayout>
      <h1 className="font-display text-2xl font-bold mb-6">Project Monitoring</h1>
      <div className="mb-6 flex gap-2 overflow-x-auto pb-1">
        {FILTERS.map((filter) => (
          <button type="button" key={filter.id} onClick={() => setSearchParams(filter.id === "all" ? {} : { status: filter.id })} className={`shrink-0 rounded-full border px-4 py-2 text-sm font-semibold ${selectedFilter === filter.id ? "border-transparent bg-brand-gradient text-white" : "border-brand-border text-gray-400 hover:border-brand-goldLight"}`}>
            {filter.label}
          </button>
        ))}
      </div>
      {error ? (
        <div className="glass rounded-2xl p-8 text-center"><p className="text-red-300">{error}</p><button onClick={load} className="mt-4 inline-flex items-center gap-2 text-sm text-brand-gold"><RefreshCw size={15} /> Try again</button></div>
      ) : !projects ? (
        <Loader />
      ) : visibleProjects.length === 0 ? (
        <div className="glass rounded-2xl p-10 text-center"><FolderKanban className="mx-auto mb-4 text-brand-gold" size={36} /><h2 className="font-semibold text-white">No projects yet</h2><p className="mt-2 text-sm text-gray-400">Client project requests will appear here as soon as they are created.</p><button onClick={load} className="mt-5 inline-flex items-center gap-2 text-sm text-brand-gold"><RefreshCw size={15} /> Refresh</button></div>
      ) : (
        <div className="space-y-4">
          {visibleProjects.map((p) => (
            <div key={p.id} className="glass rounded-xl p-5">
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                  <h3 className="font-semibold text-white">{p.project_title}</h3>
                  <p className="text-sm text-gray-400 mt-1">{p.project_description}</p>
                  <p className="mt-2 text-xs text-gray-500">Client: <span className="text-gray-300">{p.client_name}</span> · Editor: <span className="text-gray-300">{p.editor_name}</span></p>
                </div>
                <Badge tone={STATUS_TONE[p.status] || "default"}>{p.status.replace("_", " ")}</Badge>
              </div>

              {p.status === "admin_review" && !p.admin_approved && (
                <div className="flex gap-3 mt-4">
                  {p.delivery?.upload_id && (
                    <button type="button" onClick={() => preview(p.delivery.access_path)} className="text-xs text-brand-gold underline self-center">
                      Preview delivered file
                    </button>
                  )}
                  <PrimaryButton className="text-sm px-4 py-2" disabled={busyId === p.id} onClick={() => setConfirmation({ id: p.id, approve: true, title: p.project_title })}>
                    Release to Client
                  </PrimaryButton>
                  <OutlineButton className="text-sm px-4 py-2" disabled={busyId === p.id} onClick={() => setConfirmation({ id: p.id, approve: false, title: p.project_title })}>
                    Reject / Request Revision
                  </OutlineButton>
                </div>
              )}
              {p.delivery && <div className="mt-4 grid gap-3 rounded-xl border border-brand-gold/15 bg-black/30 p-4 text-xs sm:grid-cols-3">
                <div><p className="text-gray-500">Delivery Review</p><p className="mt-1 text-white">{p.delivery.original_filename}</p><p className="text-brand-gold">{p.delivery.delivery_status?.replaceAll("_", " ")}</p></div>
                <div><p className="text-gray-500">Payment Control</p><p className="mt-1 text-white">{p.payment?.currency || "LKR"} {Number(p.payment?.authorized_amount || p.payment?.amount || 0).toFixed(2)}</p><p className="text-brand-gold">{p.payment_status}</p></div>
                <div><p className="text-gray-500">Settlement Breakdown</p><p className="mt-1 text-gray-300">EditZone 12% · Editor 88%</p><p className="text-brand-gold">{p.payment?.editor_payout_status || "NOT READY"}</p></div>
              </div>}
            </div>
          ))}
        </div>
      )}
      <ConfirmModal
        open={Boolean(confirmation)}
        title={confirmation?.approve ? "Approve this final video?" : "Reject video and request revision?"}
        description={confirmation?.approve
          ? `This securely releases “${confirmation?.title || "this project"}” to its client, captures the valid PayHere authorization, records EditZone's 12% share, and creates an 88% editor payout as pending.`
          : `The editor will be asked to revise “${confirmation?.title || "this project"}”.`}
        confirmLabel={confirmation?.approve ? "Release video & payment" : "Reject & request revision"}
        danger={!confirmation?.approve}
        busy={Boolean(busyId)}
        onCancel={() => setConfirmation(null)}
        onConfirm={() => confirmation && verify(confirmation.id, confirmation.approve)}
      />
    </AdminLayout>
  );
}
