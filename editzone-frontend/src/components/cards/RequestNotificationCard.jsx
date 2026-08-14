import { useState } from "react";
import { Check, X, MessageSquare } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Badge } from "../common/UI";

export default function RequestNotificationCard({ req, onRespond }) {
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const tone = { pending: "warning", accepted: "gold", in_progress: "gold", delivered: "warning", completed: "success", rejected: "danger" }[req.status] || "default";

  return (
    <div className="glass rounded-xl p-5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h3 className="font-semibold text-white">{req.project_title}</h3>
          <p className="text-sm text-gray-400 mt-1">{req.project_description}</p>
          <div className="mt-2 flex flex-wrap gap-3 text-xs text-gray-500"><span>Requested {new Date(req.created_at).toLocaleDateString()}</span>{req.proposal_amount > 0 && <span>Rs. {Number(req.proposal_amount).toLocaleString("en-LK")}</span>}{req.proposal_delivery_days > 0 && <span>{req.proposal_delivery_days} delivery days</span>}{req.payment_status && <span>Payment: {req.payment_status.replaceAll("_", " ")}</span>}</div>
        </div>
        <Badge tone={tone}>{req.status.replace("_", " ")}</Badge>
      </div>

      <div className="flex gap-3 mt-4">
        {req.status === "pending" && (
          <>
            <button
              disabled={submitting}
              onClick={async () => {
                setSubmitting(true);
                await onRespond(req.id, "accept");
                setSubmitting(false);
              }}
              className="flex items-center gap-1 px-4 py-2 rounded-lg bg-green-500/15 text-green-400 border border-green-500/30 text-sm hover:bg-green-500/25"
            >
              <Check size={14} /> {submitting ? "Accepting…" : "Accept"}
            </button>
            <button
              onClick={() => onRespond(req.id, "reject")}
              className="flex items-center gap-1 px-4 py-2 rounded-lg bg-red-500/15 text-red-400 border border-red-500/30 text-sm hover:bg-red-500/25"
            >
              <X size={14} /> Reject
            </button>
          </>
        )}
        {!["pending", "rejected", "cancelled", "refunded", "expired"].includes(req.status) && (
          <button
            onClick={() => navigate(`/editor/chat/${req.id}`)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-gradient text-white text-sm hover:shadow-glow"
          >
            <MessageSquare size={14} /> Open Chat
          </button>
        )}
      </div>

    </div>
  );
}
