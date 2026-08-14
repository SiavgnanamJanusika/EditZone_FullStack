import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const chatUrl = new URL("../src/pages/shared/ChatPage.jsx", import.meta.url);
const paymentUrl = new URL("../src/pages/payment/PaymentPage.jsx", import.meta.url);

test("chat uses versioned editor quotes and exposes one client PayHere action", async () => {
  const source = await readFile(chatUrl, "utf8");
  assert.match(source, /!isEditor/);
  assert.match(source, /api\.post\(`\/projects\/\$\{requestId\}\/final-quote`/);
  assert.match(source, /Set Client Amount/);
  assert.match(source, /"Pay Securely"/);
  assert.equal((source.match(/"Pay Securely"/g) || []).length, 1);
  assert.match(source, /payableAmount > 0/);
  assert.doesNotMatch(source, />\s*Payment Pending\s*</);
  assert.match(source, /You save no card details on EditZone/);
});

test("chat reacts to verified payment and delivery release states", async () => {
  const source = await readFile(chatUrl, "utf8");
  assert.match(source, /payment_status_updated/);
  assert.match(source, /"SUCCESS", "CAPTURED", "RELEASED"/);
  assert.match(source, /paymentCompleted/);
  assert.match(source, /Payment verified — final output unlocked/);
});

test("payment page is driven by the immutable backend quote", async () => {
  const source = await readFile(paymentUrl, "utf8");
  assert.match(source, /quotes\/\$\{encodeURIComponent\(quoteId\)\}/);
  assert.match(source, /api\.post\("\/payments\/payhere\/create", \{ quote_id: quote\.id/);
  assert.match(source, /data\.checkout_url \|\| data\.action_url/);
  assert.match(source, /data\.payment_data \|\| data\.fields/);
  assert.match(source, /checkoutForm\.method = "POST"/);
  assert.doesNotMatch(source, /merchant_secret/);
  assert.match(source, /Client service fee \(10%\)/);
  assert.match(source, /Continue with PayHere/);
  assert.match(source, /acceptedTerms/);
  assert.doesNotMatch(source, /Number\(request\.proposal_amount \|\| 0\)/);
});

test("proposal acceptance refreshes server state and listens for participant updates", async () => {
  const source = await readFile(chatUrl, "utf8");
  assert.match(source, /proposal_updated/);
  assert.match(source, /await api\.post\(`\/requests\/\$\{requestId\}\/proposal\/accept`\)/);
  assert.match(source, /await api\.get\(`\/requests\/\$\{requestId\}`\)/);
});

test("final delivery and quote workflows have independent state and requests", async () => {
  const source = await readFile(chatUrl, "utf8");
  assert.match(source, /setFinalUploadError/);
  assert.match(source, /setQuoteError/);
  assert.doesNotMatch(source, /payments\/eligibility/);
  assert.match(source, /onProcessing: \(saved\).*setUploadStage\("processing"\)/s);
  assert.match(source, /Retry submission/);
  assert.match(source, /api\.post\(`\/requests\/\$\{requestId\}\/deliver`, \{ upload_id: media\.upload_id/);
  assert.doesNotMatch(source, /final_delivery[^\n]{0,300}final-quote/);
  assert.doesNotMatch(source, /Editor Final Delivery/);
  assert.doesNotMatch(source, /type="button" onClick=\{cancelFinalUpload\}/);
});
