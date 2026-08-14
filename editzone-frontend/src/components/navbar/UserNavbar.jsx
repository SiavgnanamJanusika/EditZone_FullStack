import { Link, useNavigate } from "react-router-dom";
import { LogOut, History, Trash2, UserCircle } from "lucide-react";
import { Logo } from "../common/UI";
import { useAuth } from "../../context/AuthContext";
import { useState } from "react";
import DeleteAccountModal from "../auth/DeleteAccountModal";
import NotificationMenu from "./NotificationMenu";
import useScrolledSurface from "../common/useScrolledSurface";

export default function UserNavbar({ search, setSearch, category, setCategory }) {
  const { user, logout } = useAuth();
  const [showDelete, setShowDelete] = useState(false);
  const navigate = useNavigate();
  const scrolled = useScrolledSurface();

  const categories = ["All", "Image Editor", "TikTok Editor", "Video Editor"];

  return (
    <nav className={`nav-shell user-nav-shell sticky top-0 z-50 ${scrolled ? "is-scrolled" : ""}`}>
      <div className="max-w-[1440px] mx-auto px-5 sm:px-8 py-3 min-h-[82px] flex flex-col lg:flex-row lg:items-center gap-3">
        <div className="flex items-center justify-between">
          <Link to="/editors"><Logo size={60} /></Link>
          <div className="flex items-center gap-3 md:hidden">
            <button onClick={() => navigate("/profile")} className="text-gray-300 hover:text-brand-gold" aria-label="Profile">
              <UserCircle size={20} />
            </button>
            <NotificationMenu mobile />
          </div>
        </div>

        {setSearch && (
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search editors by skill, location..."
            className="flex-1 min-h-11 px-5 py-2 rounded-full bg-white/[0.05] border border-white/10 text-white placeholder-gray-500 focus:outline-none focus:border-brand-goldLight focus:ring-2 focus:ring-brand-goldLight/20"
          />
        )}

        <div className="flex items-center gap-2 overflow-x-auto">
          {setCategory && categories.map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={`px-3 py-1.5 rounded-full text-xs whitespace-nowrap border transition-colors ${
                category === c
                  ? "bg-brand-gradient text-white border-transparent"
                  : "border-brand-border text-gray-300 hover:border-brand-goldLight"
              }`}
            >
              {c}
            </button>
          ))}
        </div>

        <div className="hidden md:flex items-center gap-4 ml-auto">
          <button onClick={() => navigate("/order-history")} className="nav-pill text-gray-300 hover:text-brand-gold flex items-center gap-1 text-sm">
            <History size={18} /> Orders
          </button>
          <button onClick={() => navigate("/profile")} className="nav-pill text-gray-300 hover:text-brand-gold flex items-center gap-1 text-sm">
            <UserCircle size={18} /> Profile
          </button>
          <NotificationMenu />
          <span className="rounded-full border border-white/10 bg-white/5 px-3 py-2 text-sm text-gray-300">{user?.username}</span>
          <button onClick={logout} className="rounded-full p-2.5 text-gray-400 hover:bg-red-500/10 hover:text-red-400"><LogOut size={18} /></button>
          <button onClick={() => setShowDelete(true)} title="Delete Account" className="rounded-full p-2.5 text-gray-400 hover:bg-red-500/10 hover:text-red-400"><Trash2 size={18} /></button>
        </div>
      </div>
      <DeleteAccountModal open={showDelete} onClose={() => setShowDelete(false)} />
    </nav>
  );
}
