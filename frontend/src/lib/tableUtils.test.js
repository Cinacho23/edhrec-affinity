import assert from "node:assert/strict";
import test from "node:test";

import { asNumber, compareValues, sortRows } from "./tableUtils.js";

test("unavailable numeric values follow scored rows in both sort directions", () => {
  const missing = [null, undefined, "", " ", NaN, Infinity, -Infinity, "invalid", "Infinity"];
  const rows = missing.map((z, index) => ({ id: `missing-${index}`, z }));
  rows.splice(2, 0, { id: "low", z: -1 }, { id: "zero", z: 0 }, { id: "high", z: "2" });

  assert.deepEqual(sortRows(rows, "z", "desc").slice(0, 3).map((row) => row.id), ["high", "zero", "low"]);
  assert.deepEqual(sortRows(rows, "z", "asc").slice(0, 3).map((row) => row.id), ["low", "zero", "high"]);
  for (const value of missing) {
    assert.equal(asNumber(value), null);
    assert.equal(compareValues(value, 0, "asc"), 1);
    assert.equal(compareValues(value, 0, "desc"), 1);
  }
});

test("text comparisons preserve alphabetical ordering and keep empty cells last", () => {
  const rows = [{ name: "Tokens" }, { name: null }, { name: "Artifacts" }, { name: "Vanilla" }];
  assert.deepEqual(sortRows(rows, "name", "asc").map((row) => row.name), ["Artifacts", "Tokens", "Vanilla", null]);
  assert.deepEqual(sortRows(rows, "name", "desc").map((row) => row.name), ["Vanilla", "Tokens", "Artifacts", null]);
});

test("sorting leaves source rows intact and preserves tied numeric order", () => {
  const rows = [{ id: "first", z: 2 }, { id: "second", z: "2" }, { id: "third", z: 1 }];
  assert.deepEqual(sortRows(rows, "z", "asc").map((row) => row.id), ["third", "first", "second"]);
  assert.deepEqual(rows.map((row) => row.id), ["first", "second", "third"]);
});
