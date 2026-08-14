import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Home, MapPin, MessageSquare, Sparkles, Star } from "lucide-react";
import UserNavbar from "../../components/navbar/UserNavbar";
import { EmptyState, Loader, Badge, PrimaryButton, OutlineButton } from "../../components/common/UI";
import api from "../../services/api";
import { resolveMediaUrl } from "../../services/media";
import { activeAccounts } from "../../utils/accounts";

const STATUS_TONE = {
  pending: "warning",
  accepted: "gold",
  rejected: "danger",
  in_progress: "gold",
  delivered: "warning",
  completed: "success",
  revision_requested: "warning", cancel_requested: "warning", cancelled: "danger",
  disputed: "danger", refund_pending: "warning", refunded: "success", overdue: "danger",
  expired: "default", admin_review: "gold", payment_failed: "danger",
};

function ReviewForm({ requestId, onDone }) {
  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setError("");
    if (comment.trim().length < 100) {
      setError(`Review must be at least 100 characters (currently ${comment.trim().length})`);
      return;
    }
    setSubmitting(true);
    try {
      await api.post("/reviews", { request_id: requestId, rating, comment });
      onDone();
    } catch (err) {
      setError(err.response?.data?.message || "Failed to submit review");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mt-3 p-4 rounded-lg bg-brand-panel border border-brand-border">
      <div className="flex items-center gap-1 mb-2">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} onClick={() => setRating(n)}>
            <Star size={20} className={n <= rating ? "text-brand-rating" : "text-gray-600"} fill={n <= rating ? "currentColor" : "none"} />
          </button>
        ))}
      </div>
      <textarea
        rows={3}
        placeholder="Write a review (min 100 characters)..."
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        className="w-full px-3 py-2 rounded-lg bg-brand-dark border border-brand-border text-white text-sm focus:outline-none focus:border-brand-goldLight"
      />
      <p className="text-[11px] text-gray-500 mt-1">{comment.trim().length}/100 characters minimum</p>
      {error && <p className="text-red-400 text-xs mt-1">{error}</p>}
      <PrimaryButton className="mt-2 text-sm px-4 py-2" onClick={submit} disabled={submitting}>
        {submitting ? "Submitting..." : "Submit Review"}
      </PrimaryButton>
    </div>
  );
}

export default function OrderHistoryPage() {
  const navigate = useNavigate();
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reviewingId, setReviewingId] = useState(null);
  const [reviewedIds, setReviewedIds] = useState(new Set());
  const [suggestions, setSuggestions] = useState({});

  const load = () => {
    setLoading(true);
    setError("");
    api.get("/requests/mine")
      .then(async (res) => {
        const nextRequests = res.data.requests || [];
        setRequests(nextRequests);
        const rejected = nextRequests.filter((request) => request.status === "rejected");
        const results = await Promise.allSettled(
          rejected.map((request) => api.get(`/requests/${request.id}/suggestions`)),
        );
        const nextSuggestions = {};
        rejected.forEach((request, index) => {
          if (results[index].status === "fulfilled") {
            nextSuggestions[request.id] = activeAccounts(results[index].value.data.editors);
          }
        });
        setSuggestions(nextSuggestions);
      })
      .catch((err) => {
        setRequests([]);
        setError(err.response?.data?.message || "Unable to load your orders");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="min-h-screen bg-brand-dark">
      <UserNavbar />
      <section className="max-w-4xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <h1 className="font-display text-2xl font-bold">Order History</h1>
          <OutlineButton onClick={() => navigate("/editors")} className="flex items-center gap-2 text-sm">
            <Home size={16} /> Back to Home
          </OutlineButton>
        </div>

        {loading ? (
          <Loader />
        ) : error ? (
          <div className="py-16 text-center">
            <p className="text-red-400">{error}</p>
            <button type="button" onClick={load} className="mt-4 text-sm font-semibold text-brand-gold">Try Again</button>
          </div>
        ) : requests.length === 0 ? (
          <EmptyState
            title="No projects yet"
            description="Hire an editor and your projects will appear here."
            action={<PrimaryButton onClick={() => navigate("/editors")}>Find an editor</PrimaryButton>}
          />
        ) : (
          <div className="motion-list space-y-4">
            {requests.map((r) => (
              <div key={r.id} className="glass rounded-xl p-5">
                <div className="flex items-start justify-between gap-4 flex-wrap">
                  <div>
                    <h3 className="font-semibold text-white">{r.project_title}</h3>
                    <p className="text-sm text-gray-400 mt-1">{r.project_description}</p>
                    <p className="text-xs text-gray-500 mt-2">Ordered {new Date(r.created_at).toLocaleDateString()}</p>
                    {r.proposal_amount && (
                      <div className="mt-3 rounded-lg border border-brand-gold/15 bg-brand-gold/5 p-3 text-xs">
                        <div className="flex flex-wrap gap-x-4 gap-y-1"><span className="font-semibold text-brand-goldLight">Proposal: Rs. {Number(r.proposal_amount).toLocaleString("en-LK")}</span><span className="text-gray-400">{r.proposal_delivery_days} day delivery</span></div>
                        <p className="mt-1 text-gray-400">{r.proposal_message}</p>
                      </div>
                    )}
                  </div>
                  <Badge tone={STATUS_TONE[r.status] || "default"}>{r.status.replace("_", " ")}</Badge>
                </div>

                <div className="flex flex-wrap gap-3 mt-4">
                  {(["accepted", "in_progress", "overdue", "admin_review", "revision_requested", "delivered", "cancel_requested", "disputed", "refund_pending", "completed"].includes(r.status)) && (
                    <OutlineButton onClick={() => navigate(`/chat/${r.id}`)} className="text-sm px-4 py-2 flex items-center gap-2">
                      <MessageSquare size={14} /> Open Chat
                    </OutlineButton>
                  )}
                  {["accepted", "payment_failed"].includes(r.status) && !r.paid && (!r.proposal_required || r.proposal_status === "accepted") && (
                    <PrimaryButton onClick={() => navigate(`/payment/${r.id}`)} className="text-sm px-4 py-2">
                      Pay Now
                    </PrimaryButton>
                  )}
                  {r.status === "rejected" && (
                    <PrimaryButton onClick={() => navigate("/editors")} className="text-sm px-4 py-2">
                      View More Editors
                    </PrimaryButton>
                  )}
                  {r.status === "admin_review" && <span className="rounded-lg border border-amber-300/20 bg-amber-300/10 px-4 py-2 text-xs text-amber-200">Admin reviewing video · payment on hold</span>}
                  {r.status === "delivered" && <PrimaryButton onClick={() => navigate(`/approve-work/${r.id}`)} className="text-sm px-4 py-2">Approve Video & Release Payment</PrimaryButton>}
                  {r.status === "completed" && !reviewedIds.has(r.id) && reviewingId !== r.id && (
                    <OutlineButton onClick={() => setReviewingId(r.id)} className="text-sm px-4 py-2 flex items-center gap-2">
                      <Star size={14} /> Leave a Review
                    </OutlineButton>
                  )}
                </div>

                {r.status === "rejected" && suggestions[r.id]?.length > 0 && (
                  <div className="mt-5 border-t border-brand-border pt-5">
                    <p className="flex items-center gap-2 text-sm font-semibold text-white"><Sparkles size={16} className="text-brand-gold" /> Recommended editors for this project</p>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      {suggestions[r.id].map((editor) => (
                        <button key={editor.id} type="button" onClick={() => navigate(`/editors/${editor.id}`)} className="flex items-center gap-3 rounded-xl border border-brand-border bg-brand-panel/60 p-3 text-left hover:border-brand-goldLight">
                          {editor.profile_picture ? (
                            <img src={resolveMediaUrl(editor.profile_picture)} alt="" className="h-11 w-11 rounded-full object-cover" />
                          ) : (
                            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-avatar-gradient text-sm font-bold text-brand-goldWarm">{(editor.username || "E").slice(0, 2).toUpperCase()}</span>
                          )}
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-semibold text-white">{editor.username}</span>
                            <span className="mt-0.5 flex items-center gap-2 text-[11px] text-gray-500"><span className="flex items-center gap-1"><Star size={11} className="text-brand-rating" fill="currentColor" /> {editor.rating_avg || 0}</span><span className="flex items-center gap-1"><MapPin size={11} /> {editor.location || "Sri Lanka"}</span></span>
                          </span>
                          <span className="text-xs font-semibold text-brand-gold">View</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {reviewingId === r.id && (
                  <ReviewForm
                    requestId={r.id}
                    onDone={() => {
                      setReviewedIds((prev) => new Set(prev).add(r.id));
                      setReviewingId(null);
                    }}
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
