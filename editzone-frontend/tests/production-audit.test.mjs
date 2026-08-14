import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const read = (path) => fs.readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("authenticated users are redirected away from guest-only auth routes", () => {
  const app = read("src/App.jsx");
  assert.match(app, /function GuestOnly/);
  assert.match(app, /path="\/login" element={<GuestOnly>/);
  assert.match(app, /path="\/register" element={<GuestOnly>/);
});

test("client and editor navbars use persisted notifications", () => {
  const menu = read("src/components/navbar/NotificationMenu.jsx");
  const userNavbar = read("src/components/navbar/UserNavbar.jsx");
  const editorNavbar = read("src/components/navbar/EditorNavbar.jsx");
  assert.match(menu, /api\.get\("\/notifications"\)/);
  assert.match(menu, /\/notifications\/read-all/);
  assert.match(userNavbar, /<NotificationMenu/);
  assert.match(editorNavbar, /<NotificationMenu/);
});

test("profile deletion reuses secure reauthentication modal", () => {
  const profile = read("src/pages/user/UserProfilePage.jsx");
  assert.match(profile, /<DeleteAccountModal/);
  assert.doesNotMatch(profile, /window\.prompt/);
});

test("authentication changes synchronize across browser tabs", () => {
  const auth = read("src/context/AuthContext.jsx");
  assert.match(auth, /BroadcastChannel\("editzone-auth"\)/);
  assert.match(auth, /type: "logout"/);
  assert.match(auth, /type: "account-deleted"/);
});

test("status feature uses central limits and selected-story navigation", () => {
  const manager = read("src/components/status/StatusManager.jsx");
  const viewer = read("src/components/status/StatusViewer.jsx");
  const limits = read("src/config/uploadLimits.js");
  assert.match(limits, /statusImage/);
  assert.match(limits, /statusVideo/);
  assert.match(manager, /validateStatusFile/);
  assert.match(manager, /initialIndex=/);
  assert.match(viewer, /Number\.isInteger\(initialIndex\)/);
});

test("status discovery is available to editors and clients", () => {
  const editorStatus = read("src/pages/editor/EditorStatusPage.jsx");
  const clientDiscovery = read("src/pages/user/EditorsPage.jsx");
  assert.match(editorStatus, /<StatusManager/);
  assert.match(clientDiscovery, /<StatusBar/);
});
