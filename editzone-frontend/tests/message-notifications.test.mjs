import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const socketContextUrl = new URL("../src/context/SocketContext.jsx", import.meta.url);
const chatUrl = new URL("../src/pages/shared/ChatPage.jsx", import.meta.url);
const browserNotificationsUrl = new URL("../src/services/browserNotifications.js", import.meta.url);
const serviceWorkerUrl = new URL("../public/editzone-sw.js", import.meta.url);

test("native message notifications use the authenticated socket and deduplicate receiver events", async () => {
  const source = await readFile(socketContextUrl, "utf8");
  assert.match(source, /nextSocket\.on\("message_notification"/);
  assert.match(source, /seenMessageIdsRef\.current\.has\(messageId\)/);
  assert.match(source, /String\(data\?\.sender_id\) === String\(userId\)/);
  assert.match(source, /showMessageNotification\(data, chatPath\)/);
  assert.match(source, /socketRef\.current\?\.disconnect\(\)/);
});

test("native notifications suppress the active conversation and route to the exact role-aware chat", async () => {
  const source = await readFile(socketContextUrl, "utf8");
  assert.match(source, /sameOpenChat/);
  assert.match(source, /document\.visibilityState === "visible"/);
  assert.match(source, /`\/editor\/chat\/\$\{requestId\}`/);
  assert.match(source, /`\/chat\/\$\{requestId\}`/);
  assert.doesNotMatch(source, /messagePopups|playNotificationSound/);
});

test("service worker displays Web Push and focuses the correct conversation", async () => {
  const [browserSource, workerSource] = await Promise.all([
    readFile(browserNotificationsUrl, "utf8"),
    readFile(serviceWorkerUrl, "utf8"),
  ]);
  assert.match(browserSource, /Notification\.requestPermission\(\)/);
  assert.match(browserSource, /pushManager\.subscribe/);
  assert.match(browserSource, /registration\.showNotification\("EditZone"/);
  assert.match(workerSource, /addEventListener\("push"/);
  assert.match(workerSource, /addEventListener\("notificationclick"/);
  assert.match(workerSource, /client\.navigate\(target\)/);
});

test("unread state reuses conversation read counts and existing mark_read receipts", async () => {
  const [socketSource, chatSource] = await Promise.all([
    readFile(socketContextUrl, "utf8"),
    readFile(chatUrl, "utf8"),
  ]);
  assert.match(socketSource, /api\.get\("\/chat"\)/);
  assert.match(socketSource, /item\.unread_count/);
  assert.match(socketSource, /nextSocket\.on\("messages_read"/);
  assert.match(chatSource, /socket\.emit\("mark_read"/);
  assert.match(chatSource, /workspace-unread-badge/);
});
