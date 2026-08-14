import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const chatUrl = new URL("../src/pages/shared/ChatPage.jsx", import.meta.url);

test("voice recording uploads a File before emitting message metadata", async () => {
  const source = await readFile(chatUrl, "utf8");
  assert.match(source, /MediaRecorder\.isTypeSupported/);
  assert.match(source, /recorder\.requestData\(\)/);
  assert.match(source, /new File\(\[blob\]/);
  assert.match(source, /await upload\(file, "chat_attachment"/);
  assert.match(source, /await emitMessage\(\{ upload_id: response\.data\.upload_id, file_type: "audio"/);
  assert.match(source, /client_message_id: clientMessageId/);
  assert.match(source, /uploadResponse: response/);
  assert.match(source, /if \(sent\) discardVoicePreview\(\)/);
});

test("chat images are previewed, restricted, uploaded, and emitted by ID", async () => {
  const source = await readFile(chatUrl, "utf8");
  assert.match(source, /\["jpg", "jpeg", "png", "webp"\]/);
  assert.match(source, /URL\.createObjectURL\(file\)/);
  assert.match(source, /secureUpload/);
  assert.match(source, /upload_id: response\.data\.upload_id/);
  assert.match(source, /aria-label="Open image preview"/);
  assert.match(source, /Image preview could not be loaded/);
  assert.doesNotMatch(source, /readAsDataURL/);
  assert.match(source, /const imageInputRef = useRef\(null\)/);
  assert.match(source, /ref=\{imageInputRef\}[\s\S]*?accept="image\/jpeg,image\/png,image\/webp"/);
  assert.match(source, /inputRef\.current\?\.click\(\)/);
  assert.match(source, /event\.target\.value = ""/);
});

test("view-once media has explicit state and blocks repeated UI opens", async () => {
  const source = await readFile(chatUrl, "utf8");
  assert.match(source, /openingViewOnce/);
  assert.match(source, /View Once Media already viewed/);
  assert.match(source, /disabled=\{openingViewOnce\}/);
  assert.doesNotMatch(source, /selectedFile\.type\.startsWith\("video\/"\) && <label/);
});

test("voice player falls back to persisted server duration", async () => {
  const source = await readFile(chatUrl, "utf8");
  assert.match(source, /message\.duration_seconds/);
  assert.match(source, /formatDuration\(duration\)/);
  assert.match(source, /recordingElapsedSecondsRef/);
});

test("upload progress distinguishes transfer completion from scan and message acknowledgement", async () => {
  const source = await readFile(chatUrl, "utf8");
  const media = await readFile(new URL("../src/services/media.js", import.meta.url), "utf8");
  assert.match(media, /onProcessing\?\.\(response\.data\)/);
  assert.match(media, /scan_status === "scan_failed"/);
  assert.match(media, /\/uploads\/status\/\$\{encodeURIComponent\(uploadId\)\}\/retry/);
  assert.match(source, /setVoiceStage\("processing"\)/);
  assert.match(source, /Waiting for security scan/);
  assert.match(source, /if \(sent\) discardVoicePreview\(\)/);
  assert.match(source, /Not sent — retry available/);
});
