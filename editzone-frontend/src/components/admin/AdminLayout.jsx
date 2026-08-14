import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  BarChart3, Bell, ChevronDown, ChevronLeft, ChevronRight, CircleUserRound,
  Clapperboard, CreditCard, FileText, FolderKanban, LayoutDashboard, LogOut,
  Menu, MessageSquareWarning, Moon, Repeat2, Search, ShieldCheck, Star,
  Sun, Users, X,
} from "lucide-react";
import { Logo } from "../common/UI";
import { useAuth } from "../../context/AuthContext";
import { useSocket } from "../../context/SocketContext";
import api from "../../services/api";

const links = [
  { to: "/admin", label: "Overview", icon: LayoutDashboard, end: true, keywords: "dashboard overview analytics statistics revenue" },
  { to: "/admin/users", label: "User Management", icon: Users, keywords: "clients users accounts manage ban" },
  { to: "/admin/editors", label: "Editor Management", icon: Clapperboard, keywords: "editors creators profiles skills" },
  { to: "/admin/projects", label: "Project Management", icon: FolderKanban, keywords: "projects orders delivery monitoring" },
  { to: "/admin/requests", label: "Project Requests", icon: Repeat2, keywords: "requests responses acceptance" },
  { to: "/admin/payments", label: "Payment Management", icon: CreditCard, keywords: "payments billing revenue transactions" },
  { to: "/admin/payment-protection", label: "Payment Protection", icon: ShieldCheck, keywords: "hold approval protection authorized captured refund" },
  { to: "/admin/disputes", label: "Disputes & Complaints", icon: MessageSquareWarning, keywords: "disputes complaints resolution support" },
  { to: "/admin/chat-reports", label: "Chat Reports", icon: MessageSquareWarning, keywords: "chat messages reports moderation" },
  { to: "/admin/reviews", label: "Review Management", icon: Star, keywords: "reviews ratings feedback moderation" },
  { to: "/admin/analytics", label: "Reports & Analytics", icon: BarChart3, keywords: "reports analytics metrics performance" },
  { to: "/admin/content", label: "Content Management", icon: FileText, keywords: "content pages landing about publishing" },
];
const updateLinks = [
  { label: "New Requests", status: "pending", color: "bg-brand-gold" },
  { label: "Accepted", status: "accepted", color: "bg-brand-gold" },
  { label: "Active Projects", status: "active", color: "bg-amber-400" },
  { label: "Completed", status: "completed", color: "bg-emerald-400" },
];

export default function AdminLayout({ children }) {
  const { user, logout } = useAuth();
  const { notifications: liveNotifications, setNotifications: setLiveNotifications } = useSocket() || {};
  const navigate = useNavigate();
  const location = useLocation();
  const menuRef = useRef(null);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [updatesOpen, setUpdatesOpen] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [search, setSearch] = useState("");
  const [theme, setTheme] = useState("dark");

  useEffect(() => {
    api.get("/notifications")
      .then((response) => setNotifications(response.data.notifications || []))
      .catch(() => setNotifications([]));
  }, []);

  useEffect(() => {
    setMobileOpen(false);
    setUpdatesOpen(false);
  }, [location.pathname, location.search]);

  useEffect(() => {
    const closeMenus = (event) => {
      if (!menuRef.current?.contains(event.target)) {
        setProfileOpen(false);
        setNotificationOpen(false);
        setUpdatesOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeMenus);
    return () => { document.removeEventListener("pointerdown", closeMenus); };
  }, []);

  const allNotifications = useMemo(() => {
    const live = (liveNotifications || []).map((item) => ({ ...item, is_read: false }));
    const seen = new Set();
    return [...live, ...notifications].filter((item) => {
      const key = item.id || `${item.title}-${item.body}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 20);
  }, [liveNotifications, notifications]);

  const unreadCount = allNotifications.filter((item) => !item.is_read).length;
  const activePage = links.find((item) => item.end ? location.pathname === item.to : location.pathname.startsWith(item.to)) || links[0];
  const searchResults = search.trim()
    ? links.filter((item) => `${item.label} ${item.keywords}`.toLowerCase().includes(search.trim().toLowerCase()))
    : [];

  const toggleSidebar = () => {
    const next = !collapsed;
    setCollapsed(next);
  };

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
  };

  const markAllRead = async () => {
    setNotifications((current) => current.map((item) => ({ ...item, is_read: true })));
    setLiveNotifications?.([]);
    try {
      await api.patch("/notifications/read-all");
    } catch {
      // Keep the menu usable if persisting the read receipt fails.
    }
  };

  const goToSearchResult = (result) => {
    navigate(result.to);
    setSearch("");
  };

  const Sidebar = ({ mobile = false }) => (
    <aside className={`admin-sidebar flex h-full flex-col ${mobile ? "w-[280px]" : collapsed ? "w-[88px]" : "w-[260px]"}`}>
      <div className={`flex h-20 items-center border-b border-current/10 ${collapsed && !mobile ? "justify-center px-3" : "justify-between px-5"}`}>
        <button type="button" onClick={() => navigate("/admin")} aria-label="Admin home" className="overflow-hidden">
          <Logo size={collapsed && !mobile ? 38 : 43} withText={!collapsed || mobile} />
        </button>
        {mobile && <button type="button" onClick={() => setMobileOpen(false)} className="admin-icon-button"><X size={20} /></button>}
      </div>
      <nav className="flex-1 space-y-1.5 overflow-y-auto px-3 py-5">
        {links.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} title={collapsed && !mobile ? label : undefined}
            className={({ isActive }) => `admin-side-link ${isActive ? "active" : ""} ${collapsed && !mobile ? "justify-center px-0" : ""}`}>
            <Icon size={19} className="shrink-0" />{(!collapsed || mobile) && <span>{label}</span>}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-current/10 p-3">
        <button type="button" onClick={logout} title="Logout" className={`admin-side-link w-full text-red-400 ${collapsed && !mobile ? "justify-center px-0" : ""}`}>
          <LogOut size={19} />{(!collapsed || mobile) && <span>Logout</span>}
        </button>
      </div>
    </aside>
  );

  return (
    <div className={`admin-shell ${theme === "light" ? "admin-light" : "admin-dark"} flex min-h-screen`}>
      <div className="fixed inset-y-0 left-0 z-30 hidden md:block"><Sidebar /></div>
      {mobileOpen && (
        <div className="modal-backdrop fixed inset-0 z-50 flex md:hidden">
          <button type="button" aria-label="Close menu" onClick={() => setMobileOpen(false)} className="absolute inset-0 bg-black/55 backdrop-blur-sm" />
          <div className="modal-panel relative h-full"><Sidebar mobile /></div>
        </div>
      )}

      <div className={`min-w-0 flex-1 transition-[margin] duration-300 ${collapsed ? "md:ml-[88px]" : "md:ml-[260px]"}`}>
        <header className="admin-topbar sticky top-0 z-20 flex h-20 items-center gap-3 px-4 sm:px-6">
          <button type="button" onClick={() => setMobileOpen(true)} className="admin-icon-button md:hidden" aria-label="Open navigation"><Menu size={21} /></button>
          <button type="button" onClick={toggleSidebar} className="admin-icon-button hidden md:grid" aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            {collapsed ? <ChevronRight size={20} /> : <ChevronLeft size={20} />}
          </button>
          <div className="hidden min-w-0 sm:block">
            <p className="text-[11px] font-semibold uppercase tracking-[.16em] opacity-50">Administration</p>
            <p className="truncate font-semibold">{activePage.label}</p>
          </div>

          <div className="relative mx-auto w-full max-w-xl">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 opacity-45" size={18} />
            <input type="search" value={search} onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter" && searchResults[0]) goToSearchResult(searchResults[0]); }}
              placeholder="Search admin pages…" className="admin-search w-full rounded-xl py-2.5 pl-11 pr-4 text-sm outline-none" />
            {searchResults.length > 0 && (
              <div className="admin-dropdown absolute left-0 right-0 top-[calc(100%+.5rem)] overflow-hidden rounded-xl p-1.5 shadow-2xl">
                {searchResults.map((result) => (
                  <button key={result.to} type="button" onClick={() => goToSearchResult(result)} className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm hover:bg-current/5">
                    <result.icon size={17} /><span>{result.label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div ref={menuRef} className="flex items-center gap-2">
            <div className="relative">
              <button
                type="button"
                onClick={() => {
                  setUpdatesOpen((open) => !open);
                  setNotificationOpen(false);
                  setProfileOpen(false);
                }}
                className="admin-profile-button flex min-h-[42px] items-center gap-2 rounded-xl px-3 text-sm font-semibold"
                aria-expanded={updatesOpen}
                aria-haspopup="menu"
              >
                <FolderKanban size={18} className="text-brand-gold" />
                <span className="hidden lg:inline">Updates</span>
                <ChevronDown size={14} className={`opacity-50 transition-transform ${updatesOpen ? "rotate-180" : ""}`} />
              </button>
              {updatesOpen && (
                <div role="menu" className="admin-dropdown absolute right-0 mt-2 w-56 rounded-2xl p-2 shadow-2xl">
                  <p className="px-3 pb-2 pt-1 text-[11px] font-bold uppercase tracking-[.16em] opacity-45">Project Updates</p>
                  {updateLinks.map((item) => (
                    <button
                      role="menuitem"
                      type="button"
                      key={item.status}
                      onClick={() => {
                        navigate(`/admin/projects?status=${item.status}`);
                        setUpdatesOpen(false);
                      }}
                      className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-medium hover:bg-current/5"
                    >
                      <span className={`h-2.5 w-2.5 rounded-full shadow ${item.color}`} />
                      <span className="flex-1">{item.label}</span>
                      <ChevronRight size={15} className="opacity-35" />
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button type="button" onClick={toggleTheme} className="admin-icon-button" aria-label={`Use ${theme === "dark" ? "light" : "dark"} mode`}>
              {theme === "dark" ? <Sun size={19} /> : <Moon size={19} />}
            </button>
            <div className="relative">
              <button type="button" onClick={() => { setNotificationOpen((open) => !open); setProfileOpen(false); setUpdatesOpen(false); }} className="admin-icon-button relative" aria-label="Notifications">
                <Bell size={19} />{unreadCount > 0 && <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-red-500" />}
              </button>
              {notificationOpen && (
                <div className="admin-dropdown absolute right-0 mt-2 w-[min(22rem,calc(100vw-2rem))] rounded-2xl p-3 shadow-2xl">
                  <div className="flex items-center justify-between px-2 pb-2">
                    <p className="font-semibold">Notifications</p>
                    {unreadCount > 0 && <button type="button" onClick={markAllRead} className="text-xs font-semibold text-brand-goldLight">Mark all read</button>}
                  </div>
                  <div className="max-h-80 overflow-y-auto">
                    {allNotifications.length === 0 ? <p className="px-2 py-8 text-center text-sm opacity-50">No notifications yet</p> :
                      allNotifications.map((item, index) => (
                        <div key={item.id || index} className="flex gap-3 border-t border-current/10 px-2 py-3">
                          <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${item.is_read ? "bg-current/20" : "bg-brand-goldLight"}`} />
                          <div><p className="text-sm font-semibold">{item.title}</p><p className="mt-0.5 text-xs opacity-60">{item.body}</p></div>
                        </div>
                      ))}
                  </div>
                </div>
              )}
            </div>
            <div className="relative">
              <button type="button" onClick={() => { setProfileOpen((open) => !open); setNotificationOpen(false); setUpdatesOpen(false); }} className="admin-profile-button flex items-center gap-2 rounded-xl p-1.5 pr-2.5">
                <span className="grid h-9 w-9 place-items-center rounded-lg bg-avatar-gradient font-bold text-brand-goldWarm">{(user?.username || "A").slice(0, 1).toUpperCase()}</span>
                <span className="hidden max-w-28 truncate text-left text-sm font-semibold lg:block">{user?.username || "Admin"}</span>
                <ChevronDown size={15} className="hidden opacity-50 lg:block" />
              </button>
              {profileOpen && (
                <div className="admin-dropdown absolute right-0 mt-2 w-60 rounded-2xl p-2 shadow-2xl">
                  <div className="border-b border-current/10 px-3 py-3">
                    <p className="truncate text-sm font-semibold">{user?.username || "Administrator"}</p>
                    <p className="truncate text-xs opacity-55">{user?.email || "Admin account"}</p>
                  </div>
                  <div className="px-2 py-2">
                    <div className="flex items-center gap-3 rounded-lg px-2 py-2 text-sm opacity-70"><CircleUserRound size={17} /> Administrator</div>
                    <button type="button" onClick={logout} className="mt-1 flex w-full items-center gap-3 rounded-lg px-2 py-2 text-sm text-red-400 hover:bg-red-500/10"><LogOut size={17} /> Logout</button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </header>
        <main className="admin-content min-w-0 overflow-x-auto p-4 sm:p-6 lg:p-8">{children}</main>
      </div>
    </div>
  );
}
