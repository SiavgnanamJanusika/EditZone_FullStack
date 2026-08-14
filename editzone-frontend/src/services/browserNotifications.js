import api from "./api";

const SW_PATH = "/editzone-sw.js";

export const browserNotificationsSupported = () => (
  "Notification" in window && "serviceWorker" in navigator
);

const decodeVapidKey = (value) => {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const bytes = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(bytes, (character) => character.charCodeAt(0));
};

export async function getNotificationRegistration() {
  if (!browserNotificationsSupported()) return null;
  await navigator.serviceWorker.register(SW_PATH, { scope: "/" });
  return navigator.serviceWorker.ready;
}

export async function syncPushSubscription() {
  if (!browserNotificationsSupported() || Notification.permission !== "granted" || !("PushManager" in window)) return false;
  const registration = await getNotificationRegistration();
  const response = await api.get("/notifications/push/public-key");
  if (!response.data?.enabled || !response.data?.public_key) return false;
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: decodeVapidKey(response.data.public_key),
    });
  }
  await api.post("/notifications/push/subscribe", subscription.toJSON());
  return true;
}

export async function enableBrowserNotifications() {
  if (!browserNotificationsSupported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  const permission = Notification.permission === "granted"
    ? "granted"
    : await Notification.requestPermission();
  if (permission === "granted") await syncPushSubscription().catch(() => false);
  return permission;
}

export async function unsubscribeBrowserNotifications() {
  if (!browserNotificationsSupported()) return;
  const registration = await getNotificationRegistration();
  const subscription = await registration?.pushManager?.getSubscription();
  if (!subscription) return;
  await api.delete("/notifications/push/subscription", { data: subscription.toJSON() }).catch(() => undefined);
  await subscription.unsubscribe().catch(() => undefined);
}

export async function showMessageNotification(data, chatUrl) {
  if (!browserNotificationsSupported() || Notification.permission !== "granted") return;
  const registration = await getNotificationRegistration();
  const tag = `editzone-chat-${data.request_id}`;
  const displayed = await registration.getNotifications({ tag });
  if (displayed.some((item) => String(item.data?.messageId) === String(data.id))) return;
  await registration.showNotification("EditZone", {
    body: `${data.sender_name || "EditZone member"}: ${data.preview || "Sent a message"}`,
    icon: "/editzone-logo.png",
    badge: "/favicon.png",
    tag,
    renotify: true,
    data: { messageId: String(data.id), roomId: String(data.request_id), chatUrl },
  });
}
