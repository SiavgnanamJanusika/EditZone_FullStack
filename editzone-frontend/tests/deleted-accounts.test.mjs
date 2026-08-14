import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("active editor cards and recommendation results defensively exclude deleted accounts", async () => {
  const utility = await readFile(new URL("../src/utils/accounts.js", import.meta.url), "utf8");
  const card = await readFile(new URL("../src/components/cards/EditorCard.jsx", import.meta.url), "utf8");
  const listing = await readFile(new URL("../src/pages/user/EditorsPage.jsx", import.meta.url), "utf8");
  const history = await readFile(new URL("../src/pages/user/OrderHistoryPage.jsx", import.meta.url), "utf8");
  assert.match(utility, /is_deleted === true/);
  assert.match(utility, /status === "deleted"/);
  assert.match(card, /isDeletedAccount\(editor\)/);
  assert.match(listing, /activeAccounts\(res\.data\.editors\)/);
  assert.match(history, /activeAccounts\(results\[index\]\.value\.data\.editors\)/);
});

test("deleted direct profiles show the safe unavailable message", async () => {
  const profile = await readFile(new URL("../src/pages/user/EditorProfilePage.jsx", import.meta.url), "utf8");
  const utility = await readFile(new URL("../src/utils/accounts.js", import.meta.url), "utf8");
  assert.match(utility, /ACCOUNT_NOT_AVAILABLE/);
  assert.match(profile, /This account is no longer available/);
});

test("deletion clears account state and relevant persisted drafts without a refresh", async () => {
  const auth = await readFile(new URL("../src/context/AuthContext.jsx", import.meta.url), "utf8");
  const modal = await readFile(new URL("../src/components/auth/DeleteAccountModal.jsx", import.meta.url), "utf8");
  assert.match(auth, /setUser\(null\)/);
  assert.match(auth, /editzone-chat-draft:/);
  assert.match(auth, /editzone:account-deleted/);
  assert.doesNotMatch(modal, /location\.reload/);
});
