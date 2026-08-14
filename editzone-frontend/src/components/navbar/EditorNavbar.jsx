import { Link, useNavigate, useLocation } from "react-router-dom";
import { LogOut, LayoutDashboard, Trash2, UserCircle, WalletCards } from "lucide-react";
import { Logo } from "../common/UI";
import { useAuth } from "../../context/AuthContext";
import { useState } from "react";
import DeleteAccountModal from "../auth/DeleteAccountModal";
import NotificationMenu from "./NotificationMenu";
import useScrolledSurface from "../common/useScrolledSurface";

export default function EditorNavbar() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [showDelete, setShowDelete] = useState(false);
  const scrolled = useScrolledSurface();

  const linkClass = (path) =>
    `flex items-center gap-2 px-4 py-2.5 rounded-full text-sm font-semibold transition-colors ${
      location.pathname === path ? "bg-brand-panel2 text-brand-gold" : "text-gray-300 hover:text-brand-gold"
    }`;

  return (
    <nav className={`nav-shell editor-nav-shell sticky top-0 z-50 ${scrolled ? "is-scrolled" : ""}`}>
      <div className="max-w-[1440px] mx-auto px-5 sm:px-8 min-h-[82px] flex items-center justify-between gap-3">
        <Link to="/editor/dashboard"><Logo size={60} /></Link>
        <div className="flex items-center gap-2">
          <button onClick={() => navigate("/editor/dashboard")} className={linkClass("/editor/dashboard")}>
            <LayoutDashboard size={18} /> <span className="hidden sm:inline">Dashboard</span>
          </button>
          <button onClick={() => navigate("/editor/profile")} className={linkClass("/editor/profile")}>
            <UserCircle size={18} /> <span className="hidden sm:inline">Profile</span>
          </button>
          <button onClick={() => navigate("/editor/earnings")} className={linkClass("/editor/earnings")}>
            <WalletCards size={18} /> <span className="hidden sm:inline">Earnings</span>
          </button>
        </div>
        <div className="flex items-center gap-3">
          <NotificationMenu />
          <span className="rounded-full border border-white/10 bg-white/5 px-3 py-2 text-sm text-gray-300 hidden md:inline">{user?.username}</span>
          <button onClick={logout} className="rounded-full px-3 py-2 text-gray-400 hover:bg-red-500/10 hover:text-red-400 flex items-center gap-1 text-sm">
            <LogOut size={18} /> <span className="hidden lg:inline">Logout</span>
          </button>
          <button onClick={() => setShowDelete(true)} title="Delete Account" className="rounded-full p-2.5 text-gray-400 hover:bg-red-500/10 hover:text-red-400"><Trash2 size={18} /></button>
        </div>
      </div>
      <DeleteAccountModal open={showDelete} onClose={() => setShowDelete(false)} />
    </nav>
  );
}
