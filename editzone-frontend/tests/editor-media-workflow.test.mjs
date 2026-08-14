import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("profile and portfolio uploads use owned media IDs and reset loading", async () => {
  const source = await read("src/pages/editor/EditorProfileEdit.jsx");
  assert.match(source, /fd\.append\("file", file\)/);
  assert.match(source, /fd\.append\("purpose", "profile_picture"\)/);
  assert.match(source, /profile_image_url/);
  assert.match(source, /inspectPortfolioDuration/);
  assert.match(source, /upload_id: saved\.upload_id/);
  assert.match(source, /onProcessing: \(uploadResult\).*setPortfolioStage\("processing"\)/s);
  assert.match(source, /retryUploadScan\(saved\.upload_id\)/);
  assert.match(source, /Retry without re-uploading/);
  assert.match(source, /finally \{\s*setUploadingPortfolio\(false\)/);
  assert.match(source, /window\.confirm/);
});

test("voice recording retains a retryable preview while socket reconnects", async () => {
  const source = await read("src/pages/shared/ChatPage.jsx");
  assert.match(source, /audio\/webm;codecs=opus/);
  assert.match(source, /Microphone permission was denied/);
  assert.match(source, /Ready — reconnecting before send/);
  assert.match(source, /client_message_id: clientMessageId/);
  assert.match(source, /voiceStage === "failed" \? "Retry"/);
});

test("public editor statuses open on a dedicated direct-loadable route", async () => {
  const [app, profile, bar, page] = await Promise.all([
    read("src/App.jsx"), read("src/pages/user/EditorProfilePage.jsx"),
    read("src/components/status/StatusBar.jsx"), read("src/pages/user/EditorStatusViewPage.jsx"),
  ]);
  assert.match(app, /path="\/editors\/:editorId\/status\/:statusId"/);
  assert.match(profile, /state: \{ from: location\.pathname \+ location\.search \}/);
  assert.match(bar, /\/status\/\$\{group\.statuses\[0\]\.id\}/);
  assert.match(page, /statusApi\.forEditor\(editorId\)/);
  assert.match(page, /<StatusViewer/);
  assert.match(page, /location\.state\?\.from \|\| `\/editors\/\$\{editorId\}`/);
  assert.match(page, /Status was deleted or has expired/);
});

test("status publisher keeps ready media for retry and modal respects viewport", async () => {
  const [manager, media] = await Promise.all([
    read("src/components/status/StatusManager.jsx"), read("src/services/media.js"),
  ]);
  assert.match(manager, /setReadyUpload\(saved\)/);
  assert.match(manager, /Retry Status/);
  assert.match(manager, /Retry Publish/);
  assert.match(manager, /Status published successfully\./);
  assert.match(manager, /pt-\[72px\]/);
  assert.match(manager, /sm:pt-\[100px\]/);
  assert.match(manager, /max-h-\[calc\(100dvh-96px\)\]/);
  assert.match(manager, /overflow-y-auto/);
  assert.match(manager, /object-contain/);
  assert.match(manager, /finally \{ setPublishing\(false\); \}/);
  assert.match(media, /Media processing took too long\. Check status again\./);
  assert.match(media, /data\.status === "ready"/);
});
