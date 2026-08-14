import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, BadgeCheck, Eye, Images, MapPin, MessageCircle, Play, Star, X } from "lucide-react";
import UserNavbar from "../../components/navbar/UserNavbar";
import { Loader } from "../../components/common/UI";
import { toast } from "../../components/common/UX";
import api from "../../services/api";
import { isVideoMedia, resolveMediaUrl } from "../../services/media";
import MediaViewer from "../../components/common/MediaViewer";
import { accountUnavailableMessage, isDeletedAccount } from "../../utils/accounts";
import { statusApi } from "../../services/statuses";
import StatusAvatar from "../../components/status/StatusAvatar";

const EMPTY_REQUEST_FORM = {
  project_title: "",
  project_description: "",
  content_type: "YouTube",
  source_duration_minutes: "",
  target_duration_minutes: "",
  output_format: "MP4 1080p",
  aspect_ratio: "16:9",
  style_reference: "",
  budget_min: "",
  budget_max: "",
  requested_revision_limit: 2,
};

const getRequestError = (error) => {
  const detail = error.response?.data?.detail ?? error.response?.data?.message;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg).filter(Boolean).join(" ");
  return "Failed to send request. Please check your details and try again.";
};

export default function EditorProfilePage() {
  const { editorId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [editor, setEditor] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showRequestForm, setShowRequestForm] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_REQUEST_FORM });
  const [validationErrors, setValidationErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [requestError, setRequestError] = useState("");
  const [activeReel, setActiveReel] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [statuses, setStatuses] = useState([]);

  useEffect(() => {
    setLoading(true);
    setLoadError("");
    Promise.all([
      api.get(`/editors/${editorId}`),
      api.get(`/reviews/editor/${editorId}`).catch(() => ({ data: { reviews: [] } })),
      statusApi.forEditor(editorId).catch(() => ({ data: { statuses: [] } })),
    ])
      .then(([editorResponse, reviewResponse, statusResponse]) => {
        if (isDeletedAccount(editorResponse.data)) {
          setEditor(null);
          setLoadError("This account is no longer available.");
          return;
        }
        setEditor(editorResponse.data);
        setReviews(reviewResponse.data.reviews || []);
        setStatuses(statusResponse.data.statuses || []);
      })
      .catch((error) => setLoadError(accountUnavailableMessage(error) || "Unable to load this editor profile"))
      .finally(() => setLoading(false));
  }, [editorId]);

  const closeRequestModal = useCallback(() => {
    if (submitting) return;
    setShowRequestForm(false);
    setRequestError("");
    setValidationErrors({});
    setForm({ ...EMPTY_REQUEST_FORM });
  }, [submitting]);

  useEffect(() => {
    if (!showRequestForm) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event) => {
      if (event.key === "Escape") closeRequestModal();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [closeRequestModal, showRequestForm]);

  const openRequestModal = () => {
    setRequestError("");
    setValidationErrors({});
    setForm({ ...EMPTY_REQUEST_FORM });
    setShowRequestForm(true);
  };

  const updateForm = (field, value) => {
    setForm((current) => ({ ...current, [field]: value }));
    setValidationErrors((current) => {
      if (!current[field]) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  };

  const validateRequest = () => {
    const errors = {};
    if (!form.project_title.trim()) errors.project_title = "Project title is required.";
    else if (form.project_title.trim().length < 3) errors.project_title = "Project title must contain at least 3 characters.";
    if (!form.project_description.trim()) errors.project_description = "Project description is required.";
    else if (form.project_description.trim().length < 20) errors.project_description = "Please provide at least 20 characters of project detail.";
    if (form.source_duration_minutes && Number(form.source_duration_minutes) <= 0) errors.source_duration_minutes = "Source duration must be greater than 0.";
    if (form.target_duration_minutes && Number(form.target_duration_minutes) <= 0) errors.target_duration_minutes = "Target duration must be greater than 0.";
    if (form.budget_min && Number(form.budget_min) < 0) errors.budget_min = "Minimum budget cannot be negative.";
    if (form.budget_max && Number(form.budget_max) < 0) errors.budget_max = "Maximum budget cannot be negative.";
    else if (form.budget_min && form.budget_max && Number(form.budget_max) < Number(form.budget_min)) errors.budget_max = "Maximum budget must be greater than or equal to minimum budget.";
    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const submitRequest = async (event) => {
    event.preventDefault();
    if (!validateRequest()) return;
    setSubmitting(true);
    setRequestError("");
    try {
      await api.post("/requests", {
        editor_id: editorId,
        ...form,
        source_duration_minutes: form.source_duration_minutes ? Number(form.source_duration_minutes) : null,
        target_duration_minutes: form.target_duration_minutes ? Number(form.target_duration_minutes) : null,
        budget_min: form.budget_min ? Number(form.budget_min) : null,
        budget_max: form.budget_max ? Number(form.budget_max) : null,
      });
      setForm({ ...EMPTY_REQUEST_FORM });
      setShowRequestForm(false);
      toast("Request sent! You'll be notified when the editor responds.", "success");
    } catch (error) {
      setRequestError(getRequestError(error));
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <div className="min-h-screen bg-brand-dark"><UserNavbar /><Loader /></div>;
  if (!editor) {
    return (
      <div className="min-h-screen bg-brand-dark">
        <UserNavbar />
        <div className="mx-auto max-w-md px-6 py-16 text-center">
          <p className="text-red-400">{loadError || "Editor profile is unavailable"}</p>
          <button type="button" onClick={() => navigate("/editors")} className="mt-4 text-sm font-semibold text-brand-gold">Back to Editors</button>
        </div>
      </div>
    );
  }

  const portfolioItems = editor.portfolio_items?.length
    ? editor.portfolio_items
    : (editor.portfolio_links || []).map((url, index) => ({ id: `legacy-${index}`, url, title: `Project ${index + 1}` }));

  return (
    <div className="profile-page min-h-screen bg-brand-dark">
      <UserNavbar />
      <section className="mx-auto max-w-5xl px-4 py-7 sm:px-6 sm:py-9">
        <button type="button" onClick={() => navigate("/editors")} className="mb-5 flex items-center gap-2 text-sm text-white/55 transition-colors hover:text-[#B49D50]">
          <ArrowLeft size={16} /> Back to Editors
        </button>

        <article className="editor-profile-panel mb-6 overflow-hidden rounded-2xl">
          <div className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:p-7">
            {statuses.length ? (
              <div className="shrink-0">
                <StatusAvatar editor={{ name: editor.username, profile_image: editor.profile_picture }} viewed={statuses.every((item) => item.is_viewed_by_me)} size="h-24 w-24 sm:h-28 sm:w-28" onClick={() => navigate(`/editors/${editorId}/status/${statuses[0].id}`, { state: { from: location.pathname + location.search } })} />
              </div>
            ) : editor.profile_picture ? (
              <img src={resolveMediaUrl(editor.profile_picture)} alt={`${editor.username}'s profile`} className="h-24 w-24 shrink-0 rounded-full border border-[#B49D50]/45 object-cover sm:h-28 sm:w-28" />
            ) : (
              <div className="flex h-24 w-24 shrink-0 items-center justify-center rounded-full border border-[#B49D50]/45 bg-[#111] text-2xl font-bold text-[#B49D50] sm:h-28 sm:w-28">
                {(editor.username || "E").slice(0, 2).toUpperCase()}
              </div>
            )}

            <div className="min-w-0 flex-1">
              <h1 className="flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-[#F5F5F5] sm:text-3xl">
                {editor.username}<BadgeCheck size={21} className="shrink-0 text-[#B49D50]" />
              </h1>
              <p className="mt-1.5 text-sm text-white/65">{editor.is_available !== false ? "Available for new projects" : "Not accepting new projects"}</p>
              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-white/55">
                <span className="flex items-center gap-1.5"><MapPin size={14} /> {editor.location || "Sri Lanka"}</span>
                <span className="flex items-center gap-1.5 text-[#B49D50]"><Star size={14} fill="currentColor" /> {editor.rating_avg || 0} ({editor.rating_count || 0})</span>
                <span className="flex items-center gap-1.5"><Eye size={14} /> {editor.total_views || 0} views</span>
              </div>
              {!!editor.skills?.length && <div className="mt-4 flex flex-wrap gap-2">{editor.skills.map((skill) => <span key={skill} className="editor-skill-chip">{skill}</span>)}</div>}
            </div>

            <div className="shrink-0 border-t border-[#B49D50]/15 pt-5 sm:border-l sm:border-t-0 sm:pl-7 sm:pt-0 sm:text-right">
              <p className="text-2xl font-bold text-[#B49D50]">Rs. {Number(editor.hourly_rate || 0).toLocaleString("en-LK")}<span className="ml-1 text-sm font-medium text-white/45">/hr</span></p>
              <button type="button" disabled={editor.is_available === false} onClick={openRequestModal} className="editor-request-button mt-3">
                <MessageCircle size={17} /> {editor.is_available === false ? "Unavailable" : "Send Request"}
              </button>
            </div>
          </div>

          <div className="border-t border-[#B49D50]/15 px-5 py-6 sm:px-7">
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-[#B49D50]">About</p>
            <p className="max-w-3xl text-sm leading-7 text-white/65">{editor.bio || "Creative editor focused on turning raw footage into stories people remember."}</p>
          </div>

          {portfolioItems.length > 0 && (
            <div className="border-t border-[#B49D50]/15 px-5 py-6 sm:px-7">
              <div className="mb-4 flex items-end justify-between gap-4">
                <div><p className="flex items-center gap-2 font-semibold text-[#F5F5F5]"><Images size={18} className="text-[#B49D50]" /> Portfolio & Reels</p><p className="mt-1 text-xs text-white/40">Tap an item to view the full project</p></div>
                <span className="shrink-0 text-xs text-white/40">{portfolioItems.length} projects</span>
              </div>
              <div className="reels-grid grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
                {portfolioItems.map((item, index) => (
                  <button type="button" key={item.id} onClick={() => setActiveReel({ link: item.url, index })} aria-label={`Open ${item.title}`} className="reel-card group relative aspect-[9/14] overflow-hidden rounded-xl border bg-[#080808] text-left">
                    {isVideoMedia(item.url) ? <video src={resolveMediaUrl(item.url)} muted playsInline preload="metadata" className="h-full w-full object-cover" /> : <img src={resolveMediaUrl(item.url)} alt={item.title} className="h-full w-full object-cover" />}
                    <span className="absolute inset-0 bg-gradient-to-t from-black/85 via-transparent to-transparent" />
                    <span className="absolute left-3 top-3 grid h-8 w-8 place-items-center rounded-full border border-white/15 bg-black/60 text-[#F5F5F5]"><Play size={14} fill="currentColor" /></span>
                    <span className="absolute bottom-3 left-3 right-3 truncate text-xs font-semibold text-[#F5F5F5]">{item.title}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </article>

        {reviews.length > 0 && (
          <section className="editor-profile-panel rounded-2xl p-5 sm:p-7">
            <h2 className="mb-5 font-semibold text-[#F5F5F5]">Reviews</h2>
            <div className="space-y-4">
              {reviews.map((review) => (
                <div key={review.id} className="border-b border-[#B49D50]/15 pb-4 last:border-0 last:pb-0">
                  <div className="mb-1 flex items-center gap-1 text-[#B49D50]">{Array.from({ length: review.rating }).map((_, index) => <Star key={index} size={14} fill="currentColor" />)}</div>
                  <p className="text-sm leading-6 text-white/60">{review.comment}</p>
                </div>
              ))}
            </div>
          </section>
        )}
      </section>

      {showRequestForm && (
        <div className="request-modal-backdrop fixed inset-0 z-[90] grid place-items-center p-3 sm:p-6" onMouseDown={(event) => event.target === event.currentTarget && closeRequestModal()}>
          <section role="dialog" aria-modal="true" aria-labelledby="request-modal-title" className="request-modal-panel flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl">
            <header className="flex items-start justify-between gap-5 border-b border-[#B49D50]/20 px-5 py-4 sm:px-6">
              <div><h2 id="request-modal-title" className="font-display text-xl font-bold text-[#F5F5F5]">Send a Project Request</h2><p className="mt-1 text-sm text-white/50">Share the project details with {editor.username}.</p></div>
              <button type="button" onClick={closeRequestModal} disabled={submitting} aria-label="Close request form" className="request-modal-close"><X size={19} /></button>
            </header>
            <form onSubmit={submitRequest} noValidate className="overflow-y-auto px-5 py-5 sm:px-6">
              <div className="space-y-4">
                <label className="request-label">Project title<input required minLength={3} maxLength={120} value={form.project_title} onChange={(event) => updateForm("project_title", event.target.value)} className="request-field" aria-invalid={!!validationErrors.project_title} aria-describedby={validationErrors.project_title ? "project-title-error" : undefined} placeholder="e.g. Product launch video" />{validationErrors.project_title && <span id="project-title-error" className="request-field-error">{validationErrors.project_title}</span>}</label>
                <label className="request-label">Project description<textarea required minLength={20} maxLength={5000} rows={4} value={form.project_description} onChange={(event) => updateForm("project_description", event.target.value)} className="request-field request-textarea resize-y" aria-invalid={!!validationErrors.project_description} aria-describedby={validationErrors.project_description ? "project-description-error" : undefined} placeholder="Describe the footage, goals, audience, and deliverables..." />{validationErrors.project_description && <span id="project-description-error" className="request-field-error">{validationErrors.project_description}</span>}</label>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="request-label">Content type<select value={form.content_type} onChange={(event) => updateForm("content_type", event.target.value)} className="request-field"><option>YouTube</option><option>Social Media</option><option>Advertisement</option><option>Film</option><option>Event</option><option>Other</option></select></label>
                  <label className="request-label">Aspect ratio<select value={form.aspect_ratio} onChange={(event) => updateForm("aspect_ratio", event.target.value)} className="request-field"><option>16:9</option><option>9:16</option><option>1:1</option><option>4:5</option><option>Other</option></select></label>
                  <label className="request-label">Source duration (minutes)<input type="number" min="1" value={form.source_duration_minutes} onChange={(event) => updateForm("source_duration_minutes", event.target.value)} className="request-field" aria-invalid={!!validationErrors.source_duration_minutes} placeholder="Optional" />{validationErrors.source_duration_minutes && <span className="request-field-error">{validationErrors.source_duration_minutes}</span>}</label>
                  <label className="request-label">Target duration (minutes)<input type="number" min="1" value={form.target_duration_minutes} onChange={(event) => updateForm("target_duration_minutes", event.target.value)} className="request-field" aria-invalid={!!validationErrors.target_duration_minutes} placeholder="Optional" />{validationErrors.target_duration_minutes && <span className="request-field-error">{validationErrors.target_duration_minutes}</span>}</label>
                  <label className="request-label">Output format<input maxLength={80} value={form.output_format} onChange={(event) => updateForm("output_format", event.target.value)} className="request-field" /></label>
                  <label className="request-label">Requested revisions<input type="number" min="0" max="10" value={form.requested_revision_limit} onChange={(event) => updateForm("requested_revision_limit", Number(event.target.value))} className="request-field" /></label>
                  <label className="request-label">Minimum budget (LKR)<input type="number" min="0" value={form.budget_min} onChange={(event) => updateForm("budget_min", event.target.value)} className="request-field" aria-invalid={!!validationErrors.budget_min} placeholder="Optional" />{validationErrors.budget_min && <span className="request-field-error">{validationErrors.budget_min}</span>}</label>
                  <label className="request-label">Maximum budget (LKR)<input type="number" min={form.budget_min || 0} value={form.budget_max} onChange={(event) => updateForm("budget_max", event.target.value)} className="request-field" aria-invalid={!!validationErrors.budget_max} placeholder="Optional" />{validationErrors.budget_max && <span className="request-field-error">{validationErrors.budget_max}</span>}</label>
                </div>
                <label className="request-label">Style reference<input maxLength={1000} value={form.style_reference} onChange={(event) => updateForm("style_reference", event.target.value)} className="request-field" placeholder="Link or short description (optional)" /></label>
              </div>
              {requestError && <p role="alert" className="mt-4 rounded-lg border border-red-400/30 bg-red-400/10 px-3 py-2.5 text-sm text-red-200">{requestError}</p>}
              <div className="mt-6 flex flex-col-reverse gap-3 border-t border-[#B49D50]/15 pt-5 sm:flex-row sm:justify-end">
                <button type="button" onClick={closeRequestModal} disabled={submitting} className="request-cancel-button">Cancel</button>
                <button type="submit" disabled={submitting} className="editor-request-button justify-center">{submitting ? "Sending..." : "Send Request"}</button>
              </div>
            </form>
          </section>
        </div>
      )}

      <MediaViewer item={activeReel?.link} title={activeReel ? `Project reel ${activeReel.index + 1}` : "Project reel"} onClose={() => setActiveReel(null)} />
    </div>
  );
}
