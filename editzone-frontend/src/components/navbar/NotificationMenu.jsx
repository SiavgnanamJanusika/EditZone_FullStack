import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, CheckCheck, LoaderCircle } from "lucide-react";
import api from "../../services/api";
import { useSocket } from "../../context/SocketContext";
import { toast } from "../common/UX";

function notificationText(item) {
  return item.body || item.message || "You have a new update.";
}

export default function NotificationMenu({ mobile = false }) {
  const { notifications: liveNotifications, totalUnreadMessages = 0 } = useSocket() || {};
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const menuRef = useRef(null);

  const load = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setLoading(true);
    try {
      const response = await api.get("/notifications");
      setItems(response.data.notifications || []);
    } catch (error) {
      if (!quiet) toast(error.response?.data?.message || "Unable to load notifications", "error");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => { load({ quiet: true }); }, [load]);
  useEffect(() => {
    if (liveNotifications?.length) load({ quiet: true });
  }, [liveNotifications?.length, load]);
  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (event.key === "Escape" || (menuRef.current && !menuRef.current.contains(event.target))) setOpen(false);
    };
    document.addEventListener("keydown", close);
    document.addEventListener("mousedown", close);
    return () => {
      document.removeEventListener("keydown", close);
      document.removeEventListener("mousedown", close);
    };
  }, [open]);

  const unread = items.filter((item) => !item.is_read).length;
  const badgeCount = unread + totalUnreadMessages;
  const markRead = async (item) => {
    if (item.is_read) return;
    setItems((current) => current.map((value) => value.id === item.id ? { ...value, is_read: true } : value));
    try { await api.patch(`/notifications/${item.id}/read`); }
    catch { await load({ quiet: true }); toast("Unable to mark notification as read", "error"); }
  };
  const markAllRead = async () => {
    if (!unread) return;
    setItems((current) => current.map((item) => ({ ...item, is_read: true })));
    try { await api.patch("/notifications/read-all"); }
    catch { await load({ quiet: true }); toast("Unable to update notifications", "error"); }
  };

  return <div ref={menuRef} className="relative">
    <button type="button" onClick={() => { setOpen((value) => !value); if (!open) load(); }} aria-label={`Notifications${badgeCount ? `, ${badgeCount} unread` : ""}`} aria-expanded={open} className={`relative rounded-full p-2 text-gray-300 hover:bg-white/5 hover:text-brand-gold ${mobile ? "text-brand-gold" : ""}`}>
      <Bell size={20} />{badgeCount > 0 && <span className="absolute -right-1 -top-1 grid min-h-4 min-w-4 place-items-center rounded-full bg-red-500 px-1 text-[9px] font-bold text-white">{badgeCount > 99 ? "99+" : badgeCount}</span>}
    </button>
    {open && <div className={`absolute z-[70] mt-2 w-[min(21rem,calc(100vw-1.5rem))] overflow-hidden rounded-2xl border border-white/10 bg-[#171719]/95 shadow-2xl backdrop-blur-xl ${mobile ? "right-[-5rem]" : "right-0"}`}>
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3"><div><p className="text-sm font-semibold">Notifications</p><p className="text-[11px] text-gray-500">{unread ? `${unread} unread` : "You're all caught up"}</p></div><button type="button" onClick={markAllRead} disabled={!unread} className="flex items-center gap-1 text-xs text-brand-gold disabled:opacity-40"><CheckCheck size={15} /> Mark all read</button></div>
      <div className="max-h-80 overflow-y-auto">{loading ? <LoaderCircle className="mx-auto my-10 animate-spin text-brand-gold" /> : items.length ? items.map((item) => <button type="button" key={item.id} onClick={() => markRead(item)} className={`block w-full border-b border-white/[.07] px-4 py-3 text-left last:border-0 hover:bg-white/[.04] ${item.is_read ? "opacity-60" : "bg-brand-gold/[.04]"}`}><span className="flex items-start gap-2"><span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${item.is_read ? "bg-transparent" : "bg-brand-gold"}`} /><span><span className="block text-sm font-medium text-white">{item.title || "EditZone update"}</span><span className="mt-1 block text-xs leading-5 text-gray-400">{notificationText(item)}</span>{item.created_at && <span className="mt-1 block text-[10px] text-gray-600">{new Date(item.created_at).toLocaleString()}</span>}</span></span></button>) : <p className="px-4 py-10 text-center text-sm text-gray-500">No notifications yet.</p>}</div>
    </div>}
  </div>;
}
