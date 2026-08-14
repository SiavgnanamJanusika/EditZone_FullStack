import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("API client uses credentials and refreshes once without browser token storage", async () => {
  const source = await readFile(new URL("../src/services/api.js", import.meta.url), "utf8");
  assert.match(source, /withCredentials:\s*true/);
  assert.match(source, /_refreshRetried/);
  assert.match(source, /\/auth\/refresh/);
  assert.match(source, /Authorization = `Bearer \$\{bearerAccessToken\}`/);
  assert.match(source, /withCredentials:\s*true/);
  assert.match(source, /returnTo=/);
  assert.doesNotMatch(source, /localStorage|sessionStorage/);
});
