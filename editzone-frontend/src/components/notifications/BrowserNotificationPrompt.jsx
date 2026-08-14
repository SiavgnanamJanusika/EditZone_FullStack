import { useEffect, useState } from "react";
import { BellRing, X } from "lucide-react";
import { browserNotificationsSupported, enableBrowserNotifications } from "../../services/browserNotifications";

export default function BrowserNotificationPrompt({ userId }) {
  const storageKey = `editzone:notification-prompt:${userId}`;
  const [visible, setVisible] = useState(false);
  const [permission, setPermission] = useState(() => browserNotificationsSupported() ? Notification.permission : "unsupported");
  const [working, setWorking] = useState(false);

  useEffect(() => {
    if (!userId || !browserNotificationsSupported() || Notification.permission === "granted") {
      setVisible(false);
      return;
    }
    setPermission(Notification.permission);
    setVisible(window.localStorage.getItem(storageKey) !== "dismissed");
  }, [storageKey, userId]);

  const dismiss = () => {
    window.localStorage.setItem(storageKey, "dismissed");
    setVisible(false);
  };

  const enable = async () => {
    setWorking(true);
    const result = await enableBrowserNotifications().catch(() => "error");
    setPermission(result);
    setWorking(false);
    if (result === "granted") setVisible(false);
  };

  if (!visible) return null;
  const blocked = permission === "denied";
  return (
    <aside className="browser-notification-prompt" role="status" aria-live="polite">
      <button type="button" onClick={dismiss} className="browser-notification-prompt-close" aria-label="Dismiss notification setup"><X size={16} /></button>
      <BellRing size={22} className="shrink-0 text-brand-gold" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-white">{blocked ? "Browser notifications are blocked" : "Enable message notifications?"}</p>
        <p className="mt-1 text-xs leading-5 text-white/55">{blocked ? "Enable notifications from your browser site settings." : "Get native desktop alerts when clients or editors message you."}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={dismiss} className="notification-prompt-secondary">{blocked ? "Close" : "Not Now"}</button>
          {!blocked && <button type="button" onClick={enable} disabled={working} className="notification-prompt-primary">{working ? "Enabling..." : "Enable Notifications"}</button>}
        </div>
      </div>
    </aside>
  );
}
