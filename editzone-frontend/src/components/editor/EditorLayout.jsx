import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { BellRing, CircleDollarSign, LayoutDashboard, LogOut, Menu, RefreshCw, UserCircle, X } from "lucide-react";
import { Logo } from "../common/UI";
import NotificationMenu from "../navbar/NotificationMenu";
import { useAuth } from "../../context/AuthContext";

const ITEMS = [
  ["/editor/status", "Status", BellRing],
  ["/editor/dashboard", "Dashboard", LayoutDashboard],
  ["/editor/updates", "Update", RefreshCw],
  ["/editor/profile", "Profile", UserCircle],
  ["/editor/earnings", "Earnings & Commission", CircleDollarSign],
];

export default function EditorLayout() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const linkClass = ({ isActive }) => `flex items-center gap-3 rounded-xl border px-3 py-3 text-sm font-semibold transition ${isActive ? "border-brand-gold/30 bg-brand-gold/10 text-brand-goldLight" : "border-transparent text-slate-400 hover:border-brand-gold/15 hover:bg-white/[.04] hover:text-white"}`;
  return <div className="min-h-dvh bg-brand-dark text-white lg:grid lg:grid-cols-[15.5rem_minmax(0,1fr)]">
    <header className="sticky top-0 z-40 flex min-h-16 items-center justify-between border-b border-white/10 bg-[#090909]/95 px-4 backdrop-blur-xl lg:hidden">
      <button type="button" onClick={() => setOpen(true)} aria-label="Open editor navigation" className="rounded-lg p-2 text-brand-gold"><Menu /></button>
      <Logo size={48} />
      <NotificationMenu mobile />
    </header>
    {open && <button type="button" aria-label="Close editor navigation" onClick={() => setOpen(false)} className="fixed inset-0 z-40 bg-black/70 lg:hidden" />}
    <aside className={`fixed inset-y-0 left-0 z-50 flex w-[15.5rem] flex-col border-r border-white/10 bg-[#090909]/98 p-4 shadow-2xl transition-transform lg:sticky lg:top-0 lg:h-dvh lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
      <div className="flex items-center justify-between px-1"><NavLink to="/editor/dashboard" onClick={() => setOpen(false)}><Logo size={58} /></NavLink><button type="button" onClick={() => setOpen(false)} aria-label="Close editor navigation" className="rounded-lg p-2 text-slate-400 lg:hidden"><X /></button></div>
      <p className="mb-3 mt-5 px-3 text-[10px] font-bold uppercase tracking-[.22em] text-brand-gold/70">Editor workspace</p>
      <nav aria-label="Editor workspace" className="space-y-1">{ITEMS.map(([to, label, Icon]) => <NavLink key={to} to={to} onClick={() => setOpen(false)} className={linkClass}><Icon size={18} /><span>{label}</span></NavLink>)}</nav>
      <div className="mt-auto border-t border-white/10 pt-4">
        <div className="mb-3 flex items-center gap-3 rounded-xl bg-white/[.035] p-3"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-avatar-gradient font-bold text-brand-goldWarm">{(user?.username || "E").slice(0, 2).toUpperCase()}</span><div className="min-w-0"><p className="truncate text-sm font-semibold">{user?.username || "Editor"}</p><p className="text-[11px] text-slate-500">Verified editor</p></div><NotificationMenu /></div>
        <button type="button" onClick={logout} className="flex w-full items-center gap-3 rounded-xl px-3 py-3 text-sm text-slate-400 hover:bg-red-500/10 hover:text-red-300"><LogOut size={18} /> Logout</button>
      </div>
    </aside>
    <main className="min-w-0 overflow-x-hidden"><Outlet /></main>
  </div>;
}
