import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("email verification route exists and login redirects unverified accounts", async () => {
  const app = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
  const login = await readFile(new URL("../src/pages/auth/LoginPage.jsx", import.meta.url), "utf8");
  assert.match(app, /path="\/verify-email"/);
  assert.match(login, /navigate\("\/verify-email"/);
  const verifyEmail = await readFile(new URL("../src/pages/auth/VerifyEmailPage.jsx", import.meta.url), "utf8");
  assert.match(verifyEmail, /expires in 5 minutes/);
});

test("login always settles its loading state and does not wait for a second profile request", async () => {
  const loginPage = await readFile(new URL("../src/pages/auth/LoginPage.jsx", import.meta.url), "utf8");
  const authContext = await readFile(new URL("../src/context/AuthContext.jsx", import.meta.url), "utf8");
  assert.match(loginPage, /finally\s*\{\s*setLoading\(false\)/);
  assert.match(loginPage, /Cannot connect to the server/);
  assert.match(authContext, /AUTH_REQUEST_TIMEOUT_MS/);
  assert.match(authContext, /setUser\(normalizeUser\(res\.data\.user\)\)/);
  assert.doesNotMatch(authContext, /const login[\s\S]*?await fetchMe\(\)/);
});

test("login sends the selected Client or Editor role to the backend", async () => {
  const authContext = await readFile(new URL("../src/context/AuthContext.jsx", import.meta.url), "utf8");
  const loginPage = await readFile(new URL("../src/pages/auth/LoginPage.jsx", import.meta.url), "utf8");
  assert.match(authContext, /\.\.\.\(role \? \{ role \} : \{\}\)/);
  assert.match(loginPage, /captchaToken,\s*role,/);
});

test("retired subscription bookmark redirects to order history", async () => {
  const app = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
  assert.match(app, /path="\/subscription" element=\{<Navigate to="\/order-history" replace \/>\}/);
  assert.doesNotMatch(app, /SubscriptionPage/);
});

test("login renders and resets Turnstile from the backend CAPTCHA contract", async () => {
  const loginPage = await readFile(new URL("../src/pages/auth/LoginPage.jsx", import.meta.url), "utf8");
  assert.match(loginPage, /x-captcha-required/);
  assert.match(loginPage, /captcha verification \(\?:is required\|failed\)/i);
  assert.match(loginPage, /setCaptchaRequired\(true\)/);
  assert.match(loginPage, /setCaptchaToken\(null\)/);
  assert.match(loginPage, /key=\{captchaResetKey\}/);
  assert.match(loginPage, /captchaToken/);
});

test("registration waits for OTP verification before fetching the session user", async () => {
  const authContext = await readFile(new URL("../src/context/AuthContext.jsx", import.meta.url), "utf8");
  const registerBody = authContext.match(/const register = async \(payload\) => \{([\s\S]*?)\n  \};/)?.[1] || "";
  assert.match(registerBody, /api\.post\("\/auth\/register"/);
  assert.doesNotMatch(registerBody, /fetchMe\(/);
});

test("verification persists and locks the pending registration email", async () => {
  const authContext = await readFile(new URL("../src/context/AuthContext.jsx", import.meta.url), "utf8");
  const verifyEmail = await readFile(new URL("../src/pages/auth/VerifyEmailPage.jsx", import.meta.url), "utf8");
  assert.match(authContext, /sessionStorage\.setItem\(PENDING_EMAIL_KEY, res\.data\.email\)/);
  assert.match(authContext, /user\.is_email_verified \?\? user\.email_verified/);
  assert.match(verifyEmail, /getPendingVerificationEmail/);
  assert.match(verifyEmail, /value=\{email\} readOnly required/);
  assert.match(verifyEmail, /setPendingVerificationEmail\(""\)/);
});

test("every pre-profile and protected route requires backend-backed email verification", async () => {
  const app = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
  const protectedRoute = await readFile(new URL("../src/routes/ProtectedRoute.jsx", import.meta.url), "utf8");
  assert.match(app, /function RequireLoggedIn[\s\S]*?!user\.is_email_verified[\s\S]*?\/verify-email/);
  assert.match(protectedRoute, /if \(!user\.is_email_verified\)/);
  assert.match(protectedRoute, /Navigate to="\/verify-email"/);
});

test("uploads wait for malware scanning and support multipart videos", async () => {
  const media = await readFile(new URL("../src/services/media.js", import.meta.url), "utf8");
  assert.match(media, /waitForUploadScan/);
  assert.match(media, /\/media\/\$\{encodeURIComponent\(uploadId\)\}\/status/);
  assert.match(media, /scan_status === "infected"/);
  assert.match(media, /\/uploads\/multipart\/initiate/);
});

test("profile images preview, bypass scan polling, persist from the upload response, and refresh auth state", async () => {
  const clientProfile = await readFile(new URL("../src/pages/user/UserProfilePage.jsx", import.meta.url), "utf8");
  const editorProfile = await readFile(new URL("../src/pages/editor/EditorProfileEdit.jsx", import.meta.url), "utf8");
  for (const source of [clientProfile, editorProfile]) {
    assert.match(source, /URL\.createObjectURL\(file\)/);
    assert.match(source, /secureUpload\(.*onProgress/s);
    assert.match(source, /refreshUser\(\)/);
    assert.match(source, /waitForScan: false/);
    assert.match(source, /\.data\.profile_image_url/);
    assert.doesNotMatch(source, /scanTimeoutMs:/);
    assert.match(source, /purpose", "profile_picture"[\s\S]*?secureUpload\([^;]+waitForScan: false/);
    assert.doesNotMatch(source, /profile-picture\?file_url=/);
    assert.doesNotMatch(source, /\/profile-picture"/);
  }
  assert.doesNotMatch(clientProfile, /Security scan in progress/);
  assert.doesNotMatch(clientProfile, /Retry scan/);
  assert.match(clientProfile, /profileUploadAbort\.current\?\.abort\(\)/);
  assert.match(clientProfile, /profileUploadSequence/);
  assert.match(clientProfile, /disabled=\{saving \|\| uploading\}/);
});

test("final output integration remains available but its workshop upload card is not rendered", async () => {
  const chat = await readFile(new URL("../src/pages/shared/ChatPage.jsx", import.meta.url), "utf8");
  const deliveryHandler = chat.match(/const deliverFinal = async \(event\) => \{([\s\S]*?)\n  \};/)?.[1] || "";

  for (const status of ["accepted", "in_progress", "overdue", "revision_requested"]) {
    assert.match(deliveryHandler, new RegExp(`"${status}"`));
  }
  assert.match(chat, /FINAL_OUTPUT_MAX_BYTES = 1_000_000_000/);
  assert.doesNotMatch(chat, /Editor Final Delivery/);
  assert.doesNotMatch(chat, />Upload Final Output</);
});

test("order history exposes payment only after the latest proposal is accepted", async () => {
  const history = await readFile(new URL("../src/pages/user/OrderHistoryPage.jsx", import.meta.url), "utf8");
  assert.match(history, /!r\.proposal_required \|\| r\.proposal_status === "accepted"/);
});
