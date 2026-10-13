import assert from "node:assert/strict";
import test from "node:test";

import { asNumber, compareValues, passesMin, passesMax, sortRows } from "./tableUtils.js";

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

test("build rarity sorting and filters use unrounded estimates and leave unavailable values last", () => {
  const rows = [
    { id: "common", tag_affinity_adjusted_pct: 0.2 },
    { id: "missing", tag_affinity_adjusted_pct: null },
    { id: "rare", tag_affinity_adjusted_pct: 0.01999 },
    { id: "threshold", tag_affinity_adjusted_pct: 0.02 },
    { id: "zero", tag_affinity_adjusted_pct: 0 },
  ];
  assert.deepEqual(sortRows(rows, "build_rarity", "desc").map((r) => r.id), ["rare", "threshold", "common", "missing", "zero"]);
  assert.deepEqual(sortRows(rows, "build_rarity", "asc").map((r) => r.id), ["common", "threshold", "rare", "missing", "zero"]);
  assert.deepEqual(rows.filter((r) => passesMin(r, "build_rarity", "50")).map((r) => r.id), ["rare", "threshold"]);
  assert.deepEqual(rows.filter((r) => passesMax(r, "build_rarity", "5")).map((r) => r.id), ["common"]);
  assert.equal(passesMin({}, "build_rarity", ""), true);
});

test("rarity sorting on themed and deciding rows stays aligned with that column's tag", () => {
  const rows = [
    { id: "a", theme_affinity_adjusted_pct: 0.02, decision_tag_affinity_adjusted_pct: 0.2, tag_affinity_adjusted_pct: 0.9 },
    { id: "b", theme_affinity_adjusted_pct: 0.2, decision_tag_affinity_adjusted_pct: 0.02, tag_affinity_adjusted_pct: 0.9 },
    { id: "unknown", theme_affinity_adjusted_pct: null, decision_tag_affinity_adjusted_pct: null, tag_affinity_adjusted_pct: 0.0001 },
  ];
  assert.deepEqual(sortRows(rows, "theme_build_rarity", "desc").map((r) => r.id), ["a", "b", "unknown"]);
  assert.deepEqual(sortRows(rows, "decision_build_rarity", "desc").map((r) => r.id), ["b", "a", "unknown"]);
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
