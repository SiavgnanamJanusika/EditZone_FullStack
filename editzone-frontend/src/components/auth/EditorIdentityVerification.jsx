import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, FileImage, ShieldAlert, Upload } from "lucide-react";
import api from "../../services/api";
import { ErrorText, PrimaryButton } from "../common/UI";
import LiveSelfieCapture from "./LiveSelfieCapture";

const NIC_PATTERN = /^(?:\d{12}|\d{9}[VvXx])$/;
const MAX_NIC_BYTES = 5 * 1024 * 1024;
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png"]);

function messageFrom(error, fallback) {
  const payload = error.response?.data;
  if (payload?.message) return payload.message;
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((item) => item.msg || item.message).filter(Boolean).join(". ") || fallback;
  }
  return fallback;
}

export default function EditorIdentityVerification({ nic, onVerified }) {
  const [status, setStatus] = useState(null);
  const [front, setFront] = useState(null);
  const [frontPreview, setFrontPreview] = useState("");
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [verificationState, setVerificationState] = useState("idle");
  const [error, setError] = useState("");
  const [consent, setConsent] = useState(false);

  const applyStatus = useCallback((data) => {
    setStatus(data);
    onVerified(Boolean(data.registration_allowed));
  }, [onVerified]);

  useEffect(() => {
    api.get("/editor/identity/status")
      .then(({ data }) => applyStatus(data))
      .catch((requestError) => setError(messageFrom(requestError, "Could not load identity status")));
  }, [applyStatus]);

  useEffect(() => {
    if (!front) { setFrontPreview(""); return undefined; }
    const preview = URL.createObjectURL(front);
    setFrontPreview(preview);
    return () => { URL.revokeObjectURL(preview); };
  }, [front]);

  const chooseFile = (setter) => (event) => {
    const file = event.target.files?.[0] || null;
    setError("");
    if (file && (!ALLOWED_TYPES.has(file.type) || file.size > MAX_NIC_BYTES)) {
      event.target.value = "";
      setError("NIC images must be JPG, JPEG, or PNG and no larger than 5 MB");
      setter(null);
      return;
    }
    setter(file);
  };

  const uploadNic = async () => {
    const normalizedNic = nic.trim().toUpperCase();
    if (!NIC_PATTERN.test(normalizedNic)) {
      setError("Enter a valid 12-digit or old-format Sri Lankan NIC number first");
      return;
    }
    if (!front) {
      setError("Select a clear front image of your NIC");
      return;
    }
    setUploading(true);
    setVerificationState("uploading");
    setProgress(0);
    setError("");
    const body = new FormData();
    body.append("nic_number", normalizedNic);
    body.append("nic_image", front);
    try {
      const { data } = await api.post("/verification/nic", body, {
        timeout: 45000,
        onUploadProgress: (event) => {
          if (event.total) {
            const next = Math.round((event.loaded * 100) / event.total);
            setProgress(next);
            if (next >= 100) setVerificationState("processing");
          }
        },
      });
      const refreshed = await api.get("/editor/identity/status");
      applyStatus(refreshed.data);
      setProgress(100);
      setVerificationState(data.status || (data.success ? "verified" : "unreadable"));
      if (!data.success) setError(data.message);
    } catch (uploadError) {
      const responseStatus = uploadError.response?.data?.status;
      setVerificationState(responseStatus || (uploadError.code === "ECONNABORTED" ? "ocr_unavailable" : "failed"));
      setError(messageFrom(uploadError, "The NIC verification service could not be reached. Please check your connection and try again."));
    } finally {
      setUploading(false);
    }
  };

  if (!status) {
    return (
      <div className="rounded-xl border border-brand-border p-4 text-sm text-gray-300">
        Loading secure identity verification…
        <ErrorText>{error}</ErrorText>
      </div>
    );
  }

  if (status.registration_allowed) {
    return (
      <div className="rounded-xl border border-emerald-400/40 bg-emerald-500/10 p-4 text-sm text-emerald-200">
        <div className="flex items-center gap-2 font-semibold">
          <CheckCircle2 size={20} /> NIC front image verified successfully
        </div>
      </div>
    );
  }

  if (status.manual_review) {
    return (
      <div className="rounded-xl border border-amber-400/40 bg-amber-500/10 p-4 text-sm text-amber-200">
        <div className="flex items-center gap-2 font-semibold">
          <ShieldAlert size={20} /> Identity verification is awaiting admin review
        </div>
        <p className="mt-2 text-xs">
          Your NIC front image was received securely. Registration will unlock after approval.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ErrorText>{error}</ErrorText>
      {(!status.nic_verified || status.status === "rejected") && (
        <div className="space-y-4 rounded-xl border border-brand-border bg-brand-panel/60 p-4">
          <div>
            <h2 className="font-semibold text-white">1. Verify your NIC</h2>
            <p className="mt-1 text-xs text-gray-400">
              {status.status === "rejected"
                ? "The previous review was not approved. Upload new clear images to retry."
                : "Upload a clear, uncropped front image. Keep all corners visible and avoid glare, blur, shadows, or reflections."}
            </p>
          </div>
          <label className="block text-sm text-gray-300">
            NIC front image
            <span className="mt-2 flex items-center gap-2 rounded-lg border border-dashed border-brand-border p-3">
              <FileImage size={18} />
              <input type="file" accept=".jpg,.jpeg,.png,image/jpeg,image/png" onChange={chooseFile(setFront)} disabled={uploading} className="w-full text-xs" />
            </span>
          </label>
          {frontPreview && (
            <div className="overflow-hidden rounded-xl border border-brand-border bg-black/20 p-2">
              <img src={frontPreview} alt="Selected NIC front preview" className="mx-auto max-h-56 rounded-lg object-contain" />
            </div>
          )}
          <div className="rounded-xl border border-brand-gold/20 bg-brand-gold/5 p-4 text-xs text-gray-300">
            <p className="font-semibold text-brand-goldLight">Why we collect identity data</p>
            <p className="mt-2">NIC front images are used only for editor identity verification and fraud prevention. They are never displayed publicly.</p>
            <p className="mt-2">Only authorized admins can review them through audited links that expire after 5 minutes. Documents are deleted {status.retention_days || 7} days after verification.</p>
            <label className="mt-3 flex items-start gap-2"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} className="mt-0.5" /><span>I understand and consent to this identity-verification use and retention policy.</span></label>
          </div>
          <PrimaryButton type="button" className="w-full" onClick={uploadNic} disabled={uploading || !front || !consent || !NIC_PATTERN.test(nic.trim())}>
            <Upload size={17} /> {uploading ? (verificationState === "processing" ? "Processing NIC text…" : `Uploading ${progress}%`) : verificationState === "ocr_unavailable" || verificationState === "failed" ? "Retry verification" : "Verify NIC"}
          </PrimaryButton>
          {uploading && (
            <div className="h-2 overflow-hidden rounded-full bg-black/30" role="progressbar" aria-valuenow={progress}>
              <div className="h-full bg-brand-gold transition-all" style={{ width: `${progress}%` }} />
            </div>
          )}
        </div>
      )}

      {status.nic_front_verified && !status.manual_review && (
        <>
          <div className="rounded-xl border border-emerald-400/30 bg-emerald-500/10 p-3 text-sm text-emerald-200">
            <CheckCircle2 size={18} className="mr-2 inline" /> NIC verification successful
          </div>
          <LiveSelfieCapture onVerified={onVerified} onStatus={applyStatus} />
        </>
      )}

    </div>
  );
}
