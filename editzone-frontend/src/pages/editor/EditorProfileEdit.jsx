import { useEffect, useState } from "react";
import { BadgeCheck, Camera, Images, MapPin, Play, Plus, Sparkles, Trash2, X } from "lucide-react";
import { Loader, Input, PrimaryButton, ErrorText } from "../../components/common/UI";
import api from "../../services/api";
import { retryUploadScan, secureUpload } from "../../services/media";
import { toast } from "../../components/common/UX";
import { isVideoMedia, resolveMediaUrl } from "../../services/media";
import MediaViewer from "../../components/common/MediaViewer";
import { useAuth } from "../../context/AuthContext";
import { FILE_LIMIT_MB, MB } from "../../config/uploadLimits";

const CATEGORIES = ["Image Editor", "TikTok Editor", "Video Editor"];
const inspectPortfolioDuration = (file) => new Promise((resolve, reject) => { const url = URL.createObjectURL(file); const video = document.createElement("video"); const done = (value, error) => { URL.revokeObjectURL(url); video.removeAttribute("src"); video.load(); error ? reject(error) : resolve(value); }; video.preload = "metadata"; video.onloadedmetadata = () => Number.isFinite(video.duration) && video.duration > 0 ? done(video.duration) : done(null, new Error("Unable to inspect reel duration")); video.onerror = () => done(null, new Error("Unable to inspect reel duration")); video.src = url; });

export default function EditorProfileEdit() {
  const { refreshUser } = useAuth();
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploadingPic, setUploadingPic] = useState(false);
  const [pictureProgress, setPictureProgress] = useState(0);
  const [pictureStage, setPictureStage] = useState("idle");
  const [picturePreview, setPicturePreview] = useState("");
  const [uploadingPortfolio, setUploadingPortfolio] = useState(false);
  const [portfolioProgress, setPortfolioProgress] = useState(0);
  const [portfolioStage, setPortfolioStage] = useState("idle");
  const [portfolioUpload, setPortfolioUpload] = useState(null);
  const [portfolioDraft, setPortfolioDraft] = useState(null);
  const [portfolioPreview, setPortfolioPreview] = useState("");
  const [portfolioForm, setPortfolioForm] = useState({ title: "", description: "", skills: "" });
  const [skillInput, setSkillInput] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [activeReel, setActiveReel] = useState(null);
  const [profileCompletion, setProfileCompletion] = useState(null);

  useEffect(() => {
    api.get("/editors/me/profile")
      .then((res) => setProfile(res.data))
      .catch((err) => setError(err.response?.data?.message || "Unable to load your editor profile"))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { api.get("/editors/me/dashboard").then(({ data }) => setProfileCompletion(data.profile_completion)).catch(() => undefined); }, []);
  useEffect(() => () => { if (portfolioPreview) URL.revokeObjectURL(portfolioPreview); }, [portfolioPreview]);

  const save = async (e) => {
    e?.preventDefault();
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const res = await api.put("/editors/me/profile", {
        username: profile.username,
        bio: profile.bio,
        skills: profile.skills,
        hourly_rate: parseFloat(profile.hourly_rate) || 0,
        location: profile.location,
        category: profile.category,
        is_available: profile.is_available,
      });
      setProfile((p) => ({ ...p, ...res.data }));
      await refreshUser();
      setMessage("Profile updated successfully!");
    } catch (err) {
      setError(err.response?.data?.detail?.message || err.response?.data?.detail || err.response?.data?.message || err.message || "Failed to update profile");
    } finally {
      setSaving(false);
    }
  };

  const addSkill = () => {
    if (!skillInput.trim()) return;
    setProfile((p) => ({ ...p, skills: [...(p.skills || []), skillInput.trim()] }));
    setSkillInput("");
  };

  const removeSkill = (skill) => {
    setProfile((p) => ({ ...p, skills: p.skills.filter((s) => s !== skill) }));
  };

  const uploadPicture = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const extension = file.name?.split(".").pop()?.toLowerCase();
    if ((file.type && !["image/jpeg", "image/png", "image/webp"].includes(file.type)) || !["jpg", "jpeg", "png", "webp"].includes(extension) || !file.size || file.size > FILE_LIMIT_MB.profileImage * MB) {
      toast(`Choose a JPG, JPEG, PNG, or WebP image up to ${FILE_LIMIT_MB.profileImage} MB`, "error");
      e.target.value = "";
      return;
    }
    if (picturePreview) URL.revokeObjectURL(picturePreview);
    const nextPreview = URL.createObjectURL(file);
    setPicturePreview(nextPreview);
    setUploadingPic(true); setPictureStage("uploading");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("purpose", "profile_picture");
      const res = await secureUpload(fd, { onProgress: setPictureProgress, waitForScan: false });
      const imageUrl = res.data.profile_image_url;
      if (!imageUrl) throw new Error("Profile image upload returned no image URL");
      setProfile((p) => ({ ...p, profile_picture: imageUrl }));
      URL.revokeObjectURL(nextPreview);
      setPicturePreview("");
      await refreshUser();
      setPictureStage("success");
      toast("Profile image updated", "success");
    } catch (err) {
      setPictureStage("failed");
      toast(err.response?.data?.detail?.message || err.response?.data?.detail || err.response?.data?.message || err.message || "Failed to upload profile picture", "error");
    } finally {
      setUploadingPic(false);
      setPictureProgress(0);
      e.target.value = "";
    }
  };

  const choosePortfolio = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const isVideo = file.type.startsWith("video/");
    const allowed = isVideo ? ["video/mp4", "video/webm", "video/quicktime"] : ["image/jpeg", "image/png", "image/webp"];
    const limitMb = isVideo ? FILE_LIMIT_MB.reelVideo : FILE_LIMIT_MB.reelImage;
    if (!allowed.includes(file.type) || !file.size || file.size > limitMb * MB) {
      toast(`Choose a supported ${isVideo ? "MP4/WebM video" : "JPG/PNG/WebP image"} up to ${limitMb} MB`, "error");
      e.target.value = "";
      return;
    }
    if (isVideo) { try { if (await inspectPortfolioDuration(file) > 90) { toast("Reel is longer than 90 seconds", "error"); e.target.value = ""; return; } } catch (err) { toast(err.message, "error"); e.target.value = ""; return; } }
    if (portfolioPreview) URL.revokeObjectURL(portfolioPreview);
    setPortfolioDraft(file); setPortfolioPreview(URL.createObjectURL(file)); setPortfolioProgress(0);
    setPortfolioForm({ title: file.name.replace(/\.[^.]+$/, ""), description: "", skills: "" });
    e.target.value = "";
  };
  const closePortfolio = () => { if (portfolioPreview) URL.revokeObjectURL(portfolioPreview); setPortfolioPreview(""); setPortfolioDraft(null); setPortfolioProgress(0); setPortfolioStage("idle"); setPortfolioUpload(null); };
  const uploadPortfolio = async (e) => {
    e.preventDefault();
    const file = portfolioDraft;
    if (!file || uploadingPortfolio || !portfolioForm.title.trim()) return;
    setUploadingPortfolio(true);
    try {
      let saved = portfolioUpload;
      if (!saved) {
        setPortfolioStage("uploading");
        const fd = new FormData();
        fd.append("file", file);
        fd.append("purpose", "editor_portfolio");
        const res = await secureUpload(fd, { onProgress: setPortfolioProgress, onProcessing: (uploadResult) => { setPortfolioUpload(uploadResult); setPortfolioStage("processing"); } });
        saved = res.data;
        setPortfolioUpload(saved);
      } else {
        setPortfolioStage("processing");
        await retryUploadScan(saved.upload_id);
      }
      setPortfolioStage("publishing");
      const created = await api.post("/editors/me/portfolio", { upload_id: saved.upload_id, title: portfolioForm.title.trim(), description: portfolioForm.description.trim(), skills: portfolioForm.skills.split(",").map((s) => s.trim()).filter(Boolean) });
      setProfile((p) => ({ ...p, portfolio_items: [created.data, ...(p.portfolio_items || [])], portfolio_links: [...(p.portfolio_links || []), created.data.url] }));
      closePortfolio(); toast("Portfolio item published", "success");
    } catch (err) {
      if (err.uploadResponse?.data?.upload_id) setPortfolioUpload(err.uploadResponse.data);
      setPortfolioStage("failed");
      toast(err.response?.data?.detail?.message || err.response?.data?.detail || err.message || "Failed to upload portfolio item", "error");
    } finally {
      setUploadingPortfolio(false);
    }
  };
  const deletePortfolio = async (item) => { if (!window.confirm(`Delete “${item.title}”? Only this portfolio item and its owned media will be removed.`)) return; try { await api.delete(`/editors/me/portfolio/${item.id}`); setProfile((p) => ({ ...p, portfolio_items: (p.portfolio_items || []).filter((row) => row.id !== item.id), portfolio_links: (p.portfolio_links || []).filter((url) => url !== item.url) })); toast("Portfolio item deleted", "success"); } catch (err) { toast(err.response?.data?.detail || "Unable to delete portfolio item", "error"); } };
  const editPortfolio = async (item) => { const title = window.prompt("Portfolio title", item.title); if (!title?.trim()) return; const description = window.prompt("Portfolio description", item.description || ""); if (description == null) return; try { const { data } = await api.patch(`/editors/me/portfolio/${item.id}`, { title: title.trim(), description }); setProfile((p) => ({ ...p, portfolio_items: (p.portfolio_items || []).map((row) => row.id === item.id ? data : row) })); toast("Portfolio item updated", "success"); } catch (err) { toast(err.response?.data?.detail || "Unable to update portfolio item", "error"); } };

  if (loading) return <Loader />;
  if (!profile) {
    return (
      <div>
        <div className="mx-auto max-w-md px-6 py-16 text-center text-red-400">{error || "Editor profile is unavailable"}</div>
      </div>
    );
  }

  return (
    <div className="profile-page">
      <section className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
        <div className="mb-6 flex items-end justify-between gap-4">
          <div>
            <p className="mb-1 text-xs font-bold uppercase tracking-[.2em] text-brand-goldLight">Editor studio</p>
            <h1 className="font-display text-2xl font-bold sm:text-3xl">Build your profile</h1>
          </div>
          <span className="hidden items-center gap-2 rounded-full border border-emerald-300/20 bg-emerald-300/10 px-4 py-2 text-xs font-semibold text-emerald-200 sm:flex"><Sparkles size={14} /> {profileCompletion == null ? "Live profile" : `${profileCompletion}% complete`}</span>
        </div>

        <div className="liquid-glass mb-6 overflow-hidden rounded-[2rem]">
          <div className="profile-cover relative h-36 sm:h-48" />
          <div className="px-5 pb-7 sm:px-8">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
            {picturePreview || profile.profile_picture ? (
              <img src={picturePreview || resolveMediaUrl(profile.profile_picture)} alt={`${profile.username}'s profile preview`} className="-mt-14 h-28 w-28 rounded-full border-4 border-[#121212] object-cover shadow-2xl sm:h-32 sm:w-32" />
            ) : (
              <div className="-mt-14 flex h-28 w-28 items-center justify-center rounded-full border-4 border-[#121212] bg-avatar-gradient text-2xl font-bold text-brand-goldWarm shadow-2xl sm:h-32 sm:w-32">
                {(profile.username || "E").slice(0, 2).toUpperCase()}
              </div>
            )}
            <div className="min-w-0 flex-1 pb-1">
              <p className="flex items-center gap-2 truncate font-display text-2xl font-bold text-white">{profile.username}<BadgeCheck size={20} className="text-brand-goldLight" fill="currentColor" /></p>
              <p className="mt-1 flex items-center gap-1.5 text-sm text-slate-400"><MapPin size={14} /> {profile.location || "Add your location"}</p>
            </div>
            <label className="flex w-fit cursor-pointer items-center gap-2 rounded-full border border-white/10 bg-white/[.07] px-4 py-2.5 text-sm font-semibold text-brand-goldLight transition hover:border-brand-gold/30 hover:bg-white/[.1]">
              <Camera size={16} /> {uploadingPic ? pictureProgress >= 100 ? "Saving…" : `Uploading ${pictureProgress}%` : pictureStage === "failed" ? "Retry photo" : "Change photo"}
              <input type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={uploadPicture} disabled={uploadingPic} />
            </label>
          </div>

          <div className="mt-6 rounded-2xl border border-white/[.08] bg-white/[.035] p-5">
            <p className="mb-2 text-xs font-semibold uppercase tracking-[.18em] text-emerald-300">Business About</p>
            <p className="text-sm leading-relaxed text-slate-300">{profile.bio || "Tell clients what makes your editing style distinctive."}</p>
          </div>
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-[.9fr_1.1fr]">
        <div className="liquid-glass rounded-[2rem] p-5 sm:p-7">
          <div className="mb-5">
            <p className="text-xs font-bold uppercase tracking-[.18em] text-brand-goldLight">Profile details</p>
            <h2 className="mt-1 text-lg font-semibold text-white">Business information</h2>
          </div>
          <form onSubmit={save} className="space-y-4">
            <div>
              <label className="text-xs text-gray-400 mb-1 block">Profile name</label>
              <Input
                placeholder="Profile name"
                minLength={2}
                maxLength={50}
                required
                value={profile.username || ""}
                onChange={(e) => setProfile({ ...profile, username: e.target.value })}
              />
            </div>

            <div>
              <label className="text-xs text-gray-400 mb-1 block">Category</label>
              <select
                value={profile.category}
                onChange={(e) => setProfile({ ...profile, category: e.target.value })}
                className="w-full px-4 py-2.5 rounded-lg bg-brand-panel border border-brand-border text-white focus:outline-none focus:border-brand-goldLight"
              >
                {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>

            <Input placeholder="Location" maxLength={100} value={profile.location || ""} onChange={(e) => setProfile({ ...profile, location: e.target.value })} />
            <Input type="number" min="0" max="1000000" step="0.01" placeholder="Hourly Rate (LKR)" value={profile.hourly_rate || ""} onChange={(e) => setProfile({ ...profile, hourly_rate: e.target.value })} />

            <label className="flex items-center justify-between gap-4 rounded-xl border border-white/10 bg-white/[.03] p-4"><span><span className="block text-sm font-semibold">Available for work</span><span className="mt-1 block text-xs text-slate-500">This persists to your public listing.</span></span><input type="checkbox" checked={profile.is_available !== false} onChange={(e) => setProfile({ ...profile, is_available: e.target.checked })} className="h-5 w-5 accent-amber-400" /></label>

            <textarea
              rows={4}
              maxLength={1000}
              placeholder="Bio - tell clients about your experience..."
              value={profile.bio || ""}
              onChange={(e) => setProfile({ ...profile, bio: e.target.value })}
              className="w-full px-4 py-2.5 rounded-lg bg-brand-panel border border-brand-border text-white focus:outline-none focus:border-brand-goldLight"
            />

            <div>
              <label className="text-xs text-gray-400 mb-1 block">Skills</label>
              <div className="flex gap-2 mb-2">
                <Input placeholder="Add a skill (e.g. Premiere Pro)" maxLength={50} value={skillInput} onChange={(e) => setSkillInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addSkill())} />
                <button type="button" onClick={addSkill} className="px-4 rounded-lg border border-brand-border text-brand-gold hover:border-brand-goldLight">Add</button>
              </div>
              <div className="flex flex-wrap gap-2">
                {(profile.skills || []).map((s) => (
                  <span key={s} onClick={() => removeSkill(s)} className="cursor-pointer text-xs px-2.5 py-1 rounded-full bg-brand-panel2 text-brand-gold border border-brand-border hover:border-red-400">
                    {s} ×
                  </span>
                ))}
              </div>
            </div>

            <ErrorText>{error}</ErrorText>
            {message && <p className="text-green-400 text-sm">{message}</p>}

            <PrimaryButton type="submit" disabled={saving}>{saving ? "Saving..." : "Save Profile"}</PrimaryButton>
          </form>
        </div>

        <div className="liquid-glass rounded-[2rem] p-5 sm:p-7">
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <p className="flex items-center gap-2 text-xs font-bold uppercase tracking-[.18em] text-brand-gold"><Images size={15} /> Project Reels</p>
              <h2 className="mt-1 text-lg font-semibold text-white">Show your best work</h2>
            </div>
            <span className="text-xs text-slate-500">{(profile.portfolio_items || profile.portfolio_links || []).length} items</span>
          </div>
          <div className="reels-grid grid grid-cols-2 gap-3 sm:grid-cols-3">
            {(profile.portfolio_items?.length ? profile.portfolio_items : (profile.portfolio_links || []).map((url, i) => ({ id: `legacy-${i}`, url, title: `Project reel ${i + 1}` }))).map((item, i) => (
              <div key={item.id} className="reel-card group relative aspect-[9/14] overflow-hidden rounded-2xl border border-white/10 bg-brand-panel"><button type="button" onClick={() => setActiveReel({ link: item.url, index: i })} aria-label={`Open ${item.title}`} className="h-full w-full text-left">
                {isVideoMedia(item.url) ? <video src={resolveMediaUrl(item.url)} muted playsInline preload="metadata" className="h-full w-full object-cover" /> : <img src={resolveMediaUrl(item.url)} alt={item.title} className="h-full w-full object-cover" />}
                <span className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-black/10" />
                <span className="absolute left-3 top-3 grid h-8 w-8 place-items-center rounded-full bg-black/30 text-white backdrop-blur-md"><Play size={14} fill="currentColor" /></span>
                <span className="absolute bottom-3 left-3 right-3 truncate text-xs font-semibold text-white">{item.title}</span>
              </button>{!String(item.id).startsWith("legacy-") && <div className="absolute right-2 top-2 z-10 flex gap-1 opacity-0 transition group-hover:opacity-100 focus-within:opacity-100"><button type="button" onClick={() => editPortfolio(item)} className="rounded-full bg-black/80 px-2 py-1 text-xs text-white">Edit</button><button type="button" onClick={() => deletePortfolio(item)} aria-label={`Delete ${item.title}`} className="rounded-full bg-red-950/80 p-2 text-red-200"><Trash2 size={15} /></button></div>}</div>
            ))}
            <label className="group flex aspect-[9/14] cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed border-brand-gold/25 bg-brand-gold/[.035] text-center transition hover:border-brand-gold/55 hover:bg-brand-gold/[.07]">
              <span className="grid h-11 w-11 place-items-center rounded-full bg-gradient-to-br from-brand-goldLight to-brand-goldDeep text-white shadow-lg"><Plus size={21} /></span>
              <span className="mt-3 px-2 text-xs font-semibold text-brand-goldLight">{uploadingPortfolio ? "Uploading…" : "Add a reel"}</span>
              <span className="mt-1 px-3 text-[10px] text-slate-500">Image or video</span>
              <input type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime" hidden onChange={choosePortfolio} disabled={uploadingPortfolio} />
            </label>
          </div>
        </div>
        </div>
      </section>
      <MediaViewer item={activeReel?.link} title={activeReel ? `Project reel ${activeReel.index + 1}` : "Project reel"} onClose={() => setActiveReel(null)} />
      {portfolioDraft && <div className="fixed inset-0 z-[80] grid place-items-center bg-black/75 p-4"><form onSubmit={uploadPortfolio} className="glass max-h-[92dvh] w-full max-w-xl overflow-y-auto rounded-2xl p-6"><div className="flex items-center justify-between"><h2 className="text-xl font-bold">Add Portfolio</h2><button type="button" onClick={closePortfolio} disabled={uploadingPortfolio} aria-label="Close portfolio uploader"><X /></button></div>{portfolioDraft.type.startsWith("video/") ? <video src={portfolioPreview} controls className="mt-4 max-h-64 w-full rounded-xl bg-black object-contain" /> : <img src={portfolioPreview} alt="Portfolio preview" className="mt-4 max-h-64 w-full rounded-xl bg-black object-contain" />}<Input required maxLength={120} placeholder="Title" value={portfolioForm.title} onChange={(e) => setPortfolioForm({ ...portfolioForm, title: e.target.value })} /><textarea className="mt-3 w-full rounded-xl border border-white/10 bg-black/30 p-3" maxLength={1500} rows={3} placeholder="Description" value={portfolioForm.description} onChange={(e) => setPortfolioForm({ ...portfolioForm, description: e.target.value })} /><Input placeholder="Skills/categories, comma separated" value={portfolioForm.skills} onChange={(e) => setPortfolioForm({ ...portfolioForm, skills: e.target.value })} />{uploadingPortfolio && <p className="mt-3 text-sm text-slate-400">{portfolioStage === "uploading" ? `Uploading… ${portfolioProgress}%` : portfolioStage === "processing" ? "Processing media securely…" : "Publishing portfolio item…"}</p>}<PrimaryButton type="submit" className="mt-4 w-full" disabled={uploadingPortfolio || !portfolioForm.title.trim()}>{uploadingPortfolio ? portfolioStage === "uploading" ? `Uploading… ${portfolioProgress}%` : portfolioStage === "processing" ? "Processing…" : "Publishing…" : portfolioUpload && portfolioStage === "failed" ? "Retry without re-uploading" : "Publish Portfolio Item"}</PrimaryButton></form></div>}
    </div>
  );
}
