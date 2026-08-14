import { Link } from "react-router-dom";
import { Heart, Sparkles } from "lucide-react";

export default function Footer() {
  return (
    <footer className="site-footer px-6 py-8">
      <div className="liquid-glass mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 rounded-[1.75rem] px-6 py-5 text-center sm:flex-row sm:text-left">
        <div>
          <p className="flex items-center justify-center gap-2 text-sm font-semibold text-white sm:justify-start">
            EditZone <Sparkles size={14} className="text-brand-gold" />
          </p>
          <p className="mt-1 text-xs text-slate-400">©2026 Janusika uki Kilinochchi. All rights reserved.</p>
        </div>
        <Link to="/credits" className="flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.06] px-4 py-2 text-xs font-semibold text-slate-200 transition hover:-translate-y-0.5 hover:border-brand-gold/40 hover:text-brand-goldLight">
          Full credits <Heart size={13} />
        </Link>
      </div>
    </footer>
  );
}
