import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("admin deletion modal requires reason and typed final confirmation", async () => {
  const source = await readFile(new URL("../src/components/admin/AdminAccountActionModal.jsx", import.meta.url), "utf8");
  assert.match(source, /reason\.trim\(\)\.length < 5/);
  assert.match(source, /confirmation !== keyword/);
  assert.match(source, /api\.delete\(`\/admin\/accounts\/\$\{id\}`/);
  assert.match(source, /api\.patch\(`\/admin\/accounts\/\$\{id\}\/restore`/);
  assert.match(source, /account\.email/);
});

test("user and editor management expose lifecycle filters", async () => {
  for (const path of ["../src/pages/admin/UserManagement.jsx", "../src/pages/admin/EditorManagement.jsx"]) {
    const source = await readFile(new URL(path, import.meta.url), "utf8");
    assert.match(source, /\["active", "suspended", "deleted"\]/);
    assert.match(source, /Delete Account/);
    assert.match(source, /Restore Account/);
  }
});
