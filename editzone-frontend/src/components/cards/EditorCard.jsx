import { useNavigate } from "react-router-dom";
import { Star, MapPin } from "lucide-react";
import { resolveMediaUrl } from "../../services/media";
import { isDeletedAccount } from "../../utils/accounts";

export default function EditorCard({ editor }) {
  const navigate = useNavigate();
  if (isDeletedAccount(editor)) return null;
  const initials = (editor.username || "E").slice(0, 2).toUpperCase();

  return (
    <button
      onClick={() => navigate(`/editors/${editor.id}`)}
      className="glass cinematic-card group min-h-[255px] rounded-2xl p-6 text-left w-full"
    >
      <div className="flex items-center gap-4 mb-5">
        {editor.profile_picture ? (
          <img src={resolveMediaUrl(editor.profile_picture)} alt={editor.username} className="w-20 h-20 rounded-2xl object-cover border border-white/10 transition-transform duration-500 group-hover:scale-105" />
        ) : (
          <div className="w-20 h-20 rounded-2xl bg-avatar-gradient flex items-center justify-center text-brand-goldWarm text-xl font-bold shadow-lg transition-transform duration-500 group-hover:scale-105">
            {initials}
          </div>
        )}
        <div>
          <h3 className="font-display text-lg font-bold text-white group-hover:text-brand-gold transition-colors">{editor.username}</h3>
          <div className="flex items-center gap-1 text-xs text-gray-400">
            <MapPin size={12} /> {editor.location || "Sri Lanka"}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mb-5 min-h-7">
        {(editor.skills || []).slice(0, 3).map((s) => (
          <span key={s} className="editor-skill-chip text-xs px-2.5 py-1 rounded-full bg-white/5 text-brand-gold border border-white/10">
            {s}
          </span>
        ))}
        {(!editor.skills || editor.skills.length === 0) && (
          <span className="text-[11px] text-gray-500">{editor.category}</span>
        )}
      </div>

      <div className="flex items-center justify-between text-sm mt-auto border-t border-white/10 pt-4">
        <div className="flex items-center gap-1 text-brand-rating">
          <Star size={14} fill="currentColor" />
          <span>{editor.rating_avg || 0}</span>
          <span className="text-gray-500">({editor.rating_count || 0})</span>
        </div>
        <span className="font-semibold text-brand-gold">Rs. {Number(editor.hourly_rate || 0).toLocaleString("en-LK")}/hr</span>
      </div>
    </button>
  );
}
