import assert from "node:assert/strict";
import test from "node:test";
import { COLOR_IDENTITY_ORDER, getColorIdentityRank, normalizeColorIdentity } from "./colorIdentity.js";

test("every possible colour subset has a unique rank grouped by colour count", () => {
  const colors = ["W", "U", "B", "R", "G"];
  const identities = Array.from({ length: 32 }, (_, mask) => colors.filter((_, index) => mask & (1 << index)));
  const ranks = identities.map(getColorIdentityRank);
  assert.equal(COLOR_IDENTITY_ORDER.length, 32);
  assert.deepEqual([...ranks].sort((a, b) => a - b), Array.from({ length: 32 }, (_, rank) => rank));
  for (const a of identities) {
    for (const b of identities) {
      if (a.length < b.length) assert.ok(getColorIdentityRank(a) < getColorIdentityRank(b));
    }
  }
});

test("legacy formats and reordered or repeated colours share the same identity", () => {
  for (const value of [["U", "W"], ["W", "U", "W"], "UW", "w,u", '["U", "W"]', "{W}{U}"]) {
    assert.equal(normalizeColorIdentity(value), "WU");
    assert.equal(getColorIdentityRank(value), 6);
  }
  for (const value of [[], "C", "Colorless", "colourless", "[]"]) {
    assert.equal(normalizeColorIdentity(value), "");
    assert.equal(getColorIdentityRank(value), 0);
  }
});

test("missing and invalid identities remain unknown rather than colourless", () => {
  for (const value of [null, undefined, "", " ", "—", "unknown", "WQ", 0, false, {}, [null]]) {
    assert.equal(normalizeColorIdentity(value), null);
    assert.equal(getColorIdentityRank(value), null);
  }
});
