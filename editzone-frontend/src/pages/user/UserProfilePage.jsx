import { useEffect, useRef, useState } from "react";
import { Camera, MapPin, UserRound } from "lucide-react";
import UserNavbar from "../../components/navbar/UserNavbar";
import { ErrorText, Loader, PrimaryButton, OutlineButton } from "../../components/common/UI";
import { useAuth } from "../../context/AuthContext";
import { resolveMediaUrl, secureUpload } from "../../services/media";
import api from "../../services/api";
import DeleteAccountModal from "../../components/auth/DeleteAccountModal";
import { FILE_LIMIT_MB, MB } from "../../config/uploadLimits";

const DISTRICTS = [
  "Colombo", "Gampaha", "Kalutara", "Kandy", "Matale", "Nuwara Eliya", "Galle", "Matara",
  "Hambantota", "Jaffna", "Kilinochchi", "Mannar", "Vavuniya", "Mullaitivu", "Batticaloa",
  "Ampara", "Trincomalee", "Kurunegala", "Puttalam", "Anuradhapura", "Polonnaruwa",
  "Badulla", "Monaragala", "Ratnapura", "Kegalle",
];

export default function UserProfilePage() {
  const { setUser, refreshUser } = useAuth();
  const fileInput = useRef(null);
  const profileUploadAbort = useRef(null);
  const profileUploadSequence = useRef(0);
  const photoPreviewRef = useRef("");
  const [profile, setProfile] = useState(null);
  const [form, setForm] = useState({ username: "", district: "", profile_picture: "" });
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadStage, setUploadStage] = useState("idle");
  const [selectedPhoto, setSelectedPhoto] = useState(null);
  const [photoPreview, setPhotoPreview] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [showDelete, setShowDelete] = useState(false);

  useEffect(() => {
    api.get("/users/me")
      .then((response) => {
        setProfile(response.data);
        setForm({
          username: response.data.username || "",
          district: response.data.district || "",
          profile_picture: response.data.profile_picture || "",
        });
      })
      .catch((err) => setError(err.response?.data?.message || "Unable to load profile"));
  }, []);

  useEffect(() => () => { profileUploadAbort.current?.abort(); if (photoPreviewRef.current) URL.revokeObjectURL(photoPreviewRef.current); }, []);

  const uploadPhotoFile = async (file) => {
    if (!file || uploading) return;
    profileUploadAbort.current?.abort();
    profileUploadAbort.current = new AbortController();
    const sequence = ++profileUploadSequence.current;
    setUploading(true); setUploadProgress(0); setUploadStage("uploading"); setError(""); setMessage("");
    const data = new FormData();
    data.append("file", file);
    data.append("purpose", "profile_picture");
    try {
      const response = await secureUpload(data, {
        onProgress: setUploadProgress,
        waitForScan: false,
        signal: profileUploadAbort.current.signal,
      });
      if (sequence !== profileUploadSequence.current) return;
      const imageUrl = response.data.profile_image_url;
      if (!imageUrl) throw new Error("Profile image upload returned no image URL");
      setForm((current) => ({ ...current, profile_picture: imageUrl }));
      setUser((current) => current ? { ...current, profile_picture: imageUrl } : current);
      await refreshUser();
      if (photoPreviewRef.current) URL.revokeObjectURL(photoPreviewRef.current);
      photoPreviewRef.current = "";
      setPhotoPreview("");
      setSelectedPhoto(null); setUploadStage("success"); setMessage("Profile image updated.");
    } catch (err) {
      if (sequence !== profileUploadSequence.current || err.name === "AbortError" || err.code === "ERR_CANCELED") return;
      setUploadStage("failed");
      setError(err.response?.data?.detail?.message || err.response?.data?.detail || err.response?.data?.message || err.message || "Photo upload failed");
    } finally { if (sequence === profileUploadSequence.current) setUploading(false); }
  };

  const uploadPhoto = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const extension = file.name?.split(".").pop()?.toLowerCase();
    if ((file.type && !["image/jpeg", "image/png", "image/webp"].includes(file.type)) || !["jpg", "jpeg", "png", "webp"].includes(extension)) {
      setError("Please choose a JPG, JPEG, PNG, or WebP image.");
      event.target.value = "";
      return;
    }
    if (!file.size || file.size > FILE_LIMIT_MB.profileImage * MB) {
      setError(`Profile photo must be ${FILE_LIMIT_MB.profileImage} MB or smaller.`);
      event.target.value = "";
      return;
    }
    if (photoPreview) URL.revokeObjectURL(photoPreview);
    profileUploadAbort.current?.abort();
    const nextPreview = URL.createObjectURL(file);
    photoPreviewRef.current = nextPreview;
    setSelectedPhoto(file); setPhotoPreview(nextPreview);
    await uploadPhotoFile(file);
    event.target.value = "";
  };

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const response = await api.put("/users/me", form);
      setProfile(response.data);
      setUser(response.data);
      setMessage("Profile updated successfully.");
    } catch (err) {
      setError(err.response?.data?.detail?.message || err.response?.data?.detail || err.response?.data?.message || err.message || "Unable to update profile");
    } finally {
      setSaving(false);
    }
  };
  const downloadData = async () => {
    try {
      const response = await api.get("/users/me/data-export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(response.data, null, 2)], { type: "application/json" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "editzone-my-data.json"; anchor.click(); URL.revokeObjectURL(url);
    } catch (err) { setError(err.response?.data?.message || "Data export failed"); }
  };
  const requestDeletion = async () => {
    try { await api.post("/users/me/deletion-request", { confirmation: "DELETE MY ACCOUNT", reason: "Requested from privacy settings" }); setMessage("Account deletion request recorded."); }
    catch (err) { setError(err.response?.data?.message || "Deletion request failed"); }
  };

  if (!profile && !error) return <div className="min-h-screen bg-brand-dark"><UserNavbar /><Loader /></div>;

  return (
    <div className="min-h-screen bg-brand-dark">
      <UserNavbar />
      <section className="mx-auto max-w-lg px-5 py-10">
        <div className="glass motion-panel rounded-2xl p-7 sm:p-8">
          <h1 className="font-display text-2xl font-bold text-center">My Profile</h1>
          <p className="mt-1 text-center text-sm text-gray-400">Keep your basic details up to date.</p>

          <form onSubmit={save} className="mt-7 space-y-5">
            <div className="flex flex-col items-center">
              <div className="relative">
                {photoPreview || form.profile_picture ? (
                  <img src={photoPreview || resolveMediaUrl(form.profile_picture)} alt="Profile preview" className="h-28 w-28 rounded-full border-4 border-white/10 object-cover shadow-xl" />
                ) : (
                  <div className="grid h-28 w-28 place-items-center rounded-full border-4 border-white/10 bg-brand-panel text-gray-500"><UserRound size={42} /></div>
                )}
                <button type="button" onClick={() => fileInput.current?.click()} disabled={uploading} aria-label="Change profile photo" className="absolute bottom-0 right-0 grid h-10 w-10 place-items-center rounded-full bg-brand-gradient text-white shadow-lg">
                  <Camera size={18} />
                </button>
                <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" onChange={uploadPhoto} className="hidden" />
              </div>
              <p className="mt-2 text-xs text-gray-500">{uploading ? uploadProgress >= 100 ? "Saving…" : `Uploading ${uploadProgress}%` : uploadStage === "success" ? "Upload complete" : uploadStage === "failed" ? "Upload failed — retry available" : "Tap the camera to change photo"}</p>
              {selectedPhoto && !uploading && error && <button type="button" onClick={() => uploadPhotoFile(selectedPhoto)} className="mt-2 text-xs font-semibold text-brand-goldLight">Retry upload</button>}
            </div>

            <label className="block">
              <span className="mb-2 flex items-center gap-2 text-sm text-gray-300"><UserRound size={16} /> Name</span>
              <input required minLength={2} maxLength={50} value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} className="w-full rounded-lg border border-brand-border bg-brand-panel px-4 py-2.5 text-white focus:outline-none focus:border-brand-goldLight" />
            </label>

            <label className="block">
              <span className="mb-2 flex items-center gap-2 text-sm text-gray-300"><MapPin size={16} /> Place</span>
              <select required value={form.district} onChange={(event) => setForm({ ...form, district: event.target.value })} className="w-full rounded-lg border border-brand-border bg-brand-panel px-4 py-2.5 text-white focus:outline-none focus:border-brand-goldLight">
                <option value="">Select your place</option>
                {DISTRICTS.map((district) => <option key={district} value={district}>{district}</option>)}
              </select>
            </label>

            <ErrorText>{error}</ErrorText>
            {message && <p className="text-sm text-green-400">{message}</p>}
            <PrimaryButton type="submit" disabled={saving || uploading} className="w-full">
              {saving ? "Saving..." : "Save Profile"}
            </PrimaryButton>
          </form>
        </div>
        <div className="glass mt-5 rounded-2xl p-6">
          <h2 className="font-semibold text-white">Privacy & your data</h2>
          <p className="mt-2 text-xs leading-5 text-gray-400">Identity documents are never public. Authorized admin reviews use audited 5-minute links, and documents are deleted after the verification retention period.</p>
          <div className="mt-4 grid gap-3"><OutlineButton type="button" onClick={downloadData}>Download my data</OutlineButton><OutlineButton type="button" onClick={requestDeletion}>Request account deletion</OutlineButton><button type="button" onClick={() => setShowDelete(true)} className="rounded-lg border border-red-400/40 px-4 py-2.5 text-sm text-red-300">Delete my account</button></div>
        </div>
      </section>
      <DeleteAccountModal open={showDelete} onClose={() => setShowDelete(false)} />
    </div>
  );
}
