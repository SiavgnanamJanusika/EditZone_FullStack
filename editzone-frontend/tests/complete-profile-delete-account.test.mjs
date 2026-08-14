import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("../src/pages/auth/CompleteProfilePage.jsx", import.meta.url), "utf8");
const modal = readFileSync(new URL("../src/components/auth/DeleteAccountModal.jsx", import.meta.url), "utf8");
const auth = readFileSync(new URL("../src/context/AuthContext.jsx", import.meta.url), "utf8");

test("complete profile renders a separate non-submit danger zone", () => {
  assert.match(page, /Danger Zone/);
  assert.match(page, /Delete your account and remove your personal profile information/);
  assert.match(page, /type="button" onClick=\{\(\) => setDeleteOpen\(true\)\}/);
  assert.match(page, /<\/form>\s*<section[^>]+aria-labelledby="danger-zone-title"/);
  assert.match(page, /DeleteAccountModal open=\{deleteOpen\}/);
});

test("delete modal requires confirmation and correct re-authentication", () => {
  assert.match(modal, /confirmation === "DELETE"/);
  assert.match(modal, /Confirm your password/);
  assert.match(modal, /GoogleLogin/);
  assert.match(modal, />Cancel<\/button>/);
  assert.match(modal, /Permanently Delete Account/);
  assert.match(modal, /disabled=\{!ready \|\| busy\}/);
});

test("successful deletion clears auth through the protected account endpoint and redirects", () => {
  assert.match(auth, /api\.delete\("\/account"/);
  assert.match(auth, /setUser\(null\)/);
  assert.match(modal, /navigate\("\/", \{ replace: true \}\)/);
  assert.match(modal, /Your account has been deleted successfully/);
  assert.match(modal, /setError\(/);
});
