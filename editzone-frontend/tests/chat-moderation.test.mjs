import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { containsPhoneNumber } from "../src/utils/chatModeration.js";

test("phone and contact-link evasions are blocked before frontend send", () => {
  for (const value of [
    "0771234567", "077 123 4567", "077-123-4567", "077.123.4567",
    "+94771234567", "+94 (77) 123-4567", "0094771234567", "(077) 123 4567",
    "tel:+94771234567", "https://wa.me/94771234567",
    "https://api.whatsapp.com/send?phone=94771234567", "0 7 7 1 2 3 4 5 6 7",
    "٠٧٧١٢٣٤٥٦٧",
  ]) {
    assert.equal(containsPhoneNumber(value), true, value);
  }
});

test("ordinary project numbers and non-phone links remain allowed", () => {
  for (const value of [
    "Rs 5000", "1080p", "1920x1080", "Project 1234", "Delivery in 7 days",
    "Version 2.0", "LKR 12500", "2026-08-15", "order EZ-20260807-12345",
    "3600 seconds", "https://wa.me/design-team",
  ]) {
    assert.equal(containsPhoneNumber(value), false, value);
  }
});

test("chat contact filtering is applied to text/captions but never attachment filenames", async () => {
  const frontend = await readFile(new URL("../src/pages/shared/ChatPage.jsx", import.meta.url), "utf8");
  const backend = await readFile(new URL("../../editzone-backend/app/sockets/socket_manager.py", import.meta.url), "utf8");
  assert.match(frontend, /if \(caption && containsPhoneNumber\(caption\)\)/);
  assert.doesNotMatch(frontend, /containsPhoneNumber\(file\.name\)/);
  assert.match(backend, /violation = contact_violation\(text\)/);
  assert.doesNotMatch(backend, /contact_violation\(original_name\)/);
  assert.doesNotMatch(backend, /filename_violation/);
});
