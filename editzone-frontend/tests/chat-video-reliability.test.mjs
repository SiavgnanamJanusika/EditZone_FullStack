import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const chat = readFileSync(new URL("../src/pages/shared/ChatPage.jsx", import.meta.url), "utf8");
const socket = readFileSync(new URL("../src/context/SocketContext.jsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../src/services/api.js", import.meta.url), "utf8");
const main = readFileSync(new URL("../src/main.jsx", import.meta.url), "utf8");

test("chat video flow includes preview, progress, retry, and idempotency", () => {
  assert.match(chat, /URL\.createObjectURL/);
  assert.match(chat, /uploadProgress/);
  assert.match(chat, /client_message_id/);
  assert.match(chat, /messageIdentity/);
  assert.match(chat, /textSendLockRef/);
  assert.match(chat, /mediaSendLockRef/);
  assert.match(chat, /err\?\.uploadResponse/);
  assert.match(chat, /await waitForUploadScan\(response\.data\.upload_id\)/);
  assert.match(chat, /await retryUploadScan\(media\.upload_id, 90000/);
  assert.match(chat, /retryPreview/);
  assert.match(chat, /<video[^>]+controls/);
});

test("documented backend origin is normalized to the API prefix", () => {
  assert.match(api, /configuredBaseUrl/);
  assert.match(api, /\/api\\\/v1\$/);
  assert.match(api, /"\/api\/v1"/);
});

test("conversation cleans up its room and socket reconnects with controlled backoff", () => {
  assert.match(chat, /socket\.emit\("join_chat"/);
  assert.match(chat, /if \(socket\.connected\) socket\.emit\("leave_chat"/);
  assert.match(chat, /socket\.on\("connect", joinConversation\)/);
  assert.match(socket, /RECONNECT_DELAYS_MS = \[1000, 2000, 4000, 8000, 10000\]/);
  assert.match(socket, /reconnectionAttempts:\s*Infinity/);
  assert.match(socket, /reconnectionDelayMax/);
  assert.match(socket, /terminalAuthError/);
  assert.match(socket, /\[clearReconnectTimer, clearUnread, user\?\.role, userId\]/);
  assert.doesNotMatch(chat, /location\.reload/);
  assert.doesNotMatch(chat, /Refresh to display/);
});

test("chat effects never return DOM or socket call results as React cleanups", () => {
  assert.doesNotMatch(chat, /useEffect\(\(\)\s*=>\s*bottomRef/);
  assert.doesNotMatch(chat, /return\s+socket\.(?:off|disconnect)\(/);
  assert.doesNotMatch(chat, /useEffect\s*\(\s*async/);
});

test("Google provider is not double-mounted by development StrictMode", () => {
  assert.doesNotMatch(main, /<StrictMode>/);
  assert.match(main, /<GoogleOAuthProvider/);
});
