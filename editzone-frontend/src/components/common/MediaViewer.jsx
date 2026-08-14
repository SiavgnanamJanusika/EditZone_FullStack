import { useEffect } from "react";
import { X } from "lucide-react";
import { isVideoMedia, resolveMediaUrl } from "../../services/media";

export default function MediaViewer({ item, title = "Project reel", onClose }) {
  useEffect(() => {
    if (!item) return undefined;
    const onKeyDown = (event) => event.key === "Escape" && onClose();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [item, onClose]);

  if (!item) return null;
  const source = resolveMediaUrl(item);

  return (
    <div role="dialog" aria-modal="true" aria-label={title} className="modal-backdrop fixed inset-0 z-[100] grid place-items-center bg-[#050505]/90 p-3 backdrop-blur-xl sm:p-8" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal-panel relative flex max-h-full w-full max-w-5xl flex-col overflow-hidden rounded-3xl border border-white/15 bg-[#0d0d0d] shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
          <p className="font-semibold text-white">{title}</p>
          <button type="button" onClick={onClose} aria-label="Close viewer" className="grid h-9 w-9 place-items-center rounded-full bg-white/[.07] text-slate-300 transition hover:bg-white/[.13] hover:text-white"><X size={18} /></button>
        </div>
        <div className="grid min-h-0 flex-1 place-items-center bg-black/60">
          {isVideoMedia(item) ? (
            <video key={source} src={source} controls autoPlay playsInline className="max-h-[78vh] w-full object-contain">Your browser does not support video playback.</video>
          ) : (
            <img src={source} alt={title} className="max-h-[78vh] w-full object-contain" />
          )}
        </div>
      </div>
    </div>
  );
}
