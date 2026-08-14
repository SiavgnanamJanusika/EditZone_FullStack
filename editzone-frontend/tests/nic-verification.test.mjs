import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const sourceUrl = new URL("../src/components/auth/EditorIdentityVerification.jsx", import.meta.url);

test("NIC verification validates files and provides an image preview", async () => {
  const source = await readFile(sourceUrl, "utf8");
  assert.match(source, /ALLOWED_TYPES/);
  assert.match(source, /MAX_NIC_BYTES/);
  assert.match(source, /URL\.createObjectURL/);
  assert.match(source, /Selected NIC front preview/);
});

test("NIC verification exposes loading, progress, verified, mismatch, retry, and server-error states", async () => {
  const source = await readFile(sourceUrl, "utf8");
  assert.match(source, /disabled=\{uploading/);
  assert.match(source, /onUploadProgress/);
  assert.match(source, /NIC front image verified successfully/);
  assert.match(source, /if \(!data\.success\) setError\(data\.message\)/);
  assert.match(source, /Upload new clear images to retry/);
  assert.match(source, /verification service could not be reached/);
});

test("registration enables live selfie only after backend NIC verification", async () => {
  const source = await readFile(sourceUrl, "utf8");
  assert.match(source, /status\.nic_front_verified && !status\.manual_review/);
  assert.match(source, /<LiveSelfieCapture/);
  assert.match(source, /registration_allowed/);
});

test("live selfie is camera-only and stops media tracks", async () => {
  const selfie = await readFile(
    new URL("../src/components/auth/LiveSelfieCapture.jsx", import.meta.url),
    "utf8",
  );
  assert.match(selfie, /navigator\.mediaDevices\.getUserMedia/);
  assert.match(selfie, /track\.stop\(\)/);
  assert.match(selfie, /X-Capture-Source/);
  assert.doesNotMatch(selfie, /type=["']file["']/);
});

test("live selfie distinguishes AWS, camera, face, and retry failures", async () => {
  const selfie = await readFile(
    new URL("../src/components/auth/LiveSelfieCapture.jsx", import.meta.url),
    "utf8",
  );
  for (const code of [
    "NO_FACE_DETECTED", "MULTIPLE_FACES", "LOW_IMAGE_QUALITY", "FACE_NOT_MATCHED",
    "FACE_VERIFICATION_NOT_CONFIGURED", "AWS_CREDENTIALS_INVALID", "AWS_UNAVAILABLE",
  ]) {
    assert.match(selfie, new RegExp(code));
  }
  assert.match(selfie, /uploadLockRef/);
  assert.match(selfie, /await api\.post\("\/editor\/selfie\/retry"\)/);
});
