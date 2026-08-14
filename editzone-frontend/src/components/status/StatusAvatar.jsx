import { resolveMediaUrl } from "../../services/media";

export default function StatusAvatar({ editor, viewed = false, size = "h-16 w-16", onClick, label }) {
  const content = editor.profile_image ? <img src={resolveMediaUrl(editor.profile_image)} alt="" className="h-full w-full rounded-full object-cover" /> : <span className="grid h-full w-full place-items-center rounded-full bg-avatar-gradient font-bold text-brand-gold">{(editor.name || "E").slice(0, 2).toUpperCase()}</span>;
  return <button type="button" onClick={onClick} aria-label={label || `View ${editor.name}'s status`} className={`rounded-full p-[3px] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-gold ${viewed ? "bg-white/20" : "bg-brand-gradient"}`}><span className={`block rounded-full border-2 border-brand-dark bg-brand-dark p-0.5 ${size}`}>{content}</span></button>;
}
