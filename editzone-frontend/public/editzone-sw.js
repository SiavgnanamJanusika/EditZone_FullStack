const notificationOptions = (data) => ({
  body: data.body || "You received a new message",
  icon: "/editzone-logo.png",
  badge: "/favicon.png",
  tag: data.tag || `editzone-chat-${data.room_id || "message"}`,
  renotify: true,
  data: {
    messageId: String(data.message_id || ""),
    roomId: String(data.room_id || ""),
    chatUrl: data.chat_url || "/",
  },
});

self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const activeSameChat = windows.some((client) => client.focused && new URL(client.url).pathname === data.chat_url);
    if (activeSameChat) return;
    const options = notificationOptions(data);
    const displayed = await self.registration.getNotifications({ tag: options.tag });
    if (displayed.some((item) => item.data?.messageId === options.data.messageId)) return;
    await self.registration.showNotification(data.title || "EditZone", options);
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.chatUrl || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (windows) => {
    for (const client of windows) {
      if ("navigate" in client) await client.navigate(target);
      if ("focus" in client) return client.focus();
    }
    return self.clients.openWindow ? self.clients.openWindow(target) : undefined;
  }));
});
