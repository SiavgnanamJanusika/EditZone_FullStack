import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("editor workspace replaces editor subscription with earnings commission", async () => {
  const [app, layout] = await Promise.all([read("src/App.jsx"), read("src/components/editor/EditorLayout.jsx")]);
  assert.match(app, /path="\/editor"[\s\S]*?<EditorLayout/);
  for (const path of ["status", "dashboard", "updates", "profile", "earnings"]) assert.match(app, new RegExp(`path="${path}"`));
  assert.doesNotMatch(app, /path="subscription" element=\{<EditorSubscriptionPage/);
  assert.equal((layout.match(/^  \["\/editor\//gm) || []).length, 5);
  assert.match(layout, /Earnings & Commission/);
  assert.match(layout, /<Outlet/);
  assert.match(layout, /<NotificationMenu/);
});

test("dashboard is summary-only and update owns request actions", async () => {
  const [dashboard, updates] = await Promise.all([read("src/pages/editor/EditorDashboard.jsx"), read("src/pages/editor/EditorUpdatesPage.jsx")]);
  assert.match(dashboard, /\/editors\/me\/dashboard/);
  assert.doesNotMatch(dashboard, /<StatusManager|<RequestNotificationCard/);
  assert.match(updates, /\/requests\/mine/);
  assert.match(updates, /\/respond/);
  for (const label of ["New Requests", "Accepted", "Active Projects", "Closed"]) assert.match(updates, new RegExp(label));
});

test("status videos are checked at 90 seconds before upload", async () => {
  const manager = await read("src/components/status/StatusManager.jsx");
  assert.match(manager, /inspectVideoDuration/);
  assert.match(manager, /duration > 90/);
  assert.match(manager, /Status videos can be up to 1 minute 30 seconds/);
  assert.match(manager, /Status preview/);
});
