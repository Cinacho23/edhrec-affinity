import assert from "node:assert/strict";
import test from "node:test";

import {
  formatAdjustedAffinity,
  formatAffinityInterval,
  formatAffinityProbability,
} from "./affinityDisplay.js";

test("affinity display treats model fields as probabilities, including zero and one", () => {
  assert.equal(formatAffinityProbability(0), "0.00%");
  assert.equal(formatAffinityProbability(1), "100.00%");
  assert.equal(formatAffinityProbability("0.2"), "20.00%");
  for (const missing of [null, undefined, "", " ", false, [], NaN, Infinity, -0.1, 20]) {
    assert.equal(formatAffinityProbability(missing), "—");
  }
});

test("adjusted display distinguishes fitted estimates from explicit raw fallbacks", () => {
  assert.equal(formatAdjustedAffinity({ tag_affinity_adjusted_pct: 0.15, affinity_model_status: "fitted" }), "15.00%");
  assert.equal(formatAdjustedAffinity({ tag_affinity_adjusted_pct: 0.2, affinity_model_status: "fallback_sparse" }), "20.00% (unadjusted)");
  assert.equal(formatAdjustedAffinity({ tag_affinity_pct: 0.2 }), "—");
});

test("interval display requires two ordered finite probability bounds", () => {
  assert.equal(formatAffinityInterval({ tag_affinity_lower_pct: 0, tag_affinity_upper_pct: 1 }), "0.00%–100.00%");
  assert.equal(formatAffinityInterval({ tag_affinity_lower_pct: 0.1, tag_affinity_upper_pct: 0.2 }), "10.00%–20.00%");
  for (const row of [{}, { tag_affinity_lower_pct: 0.1 }, { tag_affinity_lower_pct: 0.3, tag_affinity_upper_pct: 0.2 }, { tag_affinity_lower_pct: NaN, tag_affinity_upper_pct: 0.2 }]) {
    assert.equal(formatAffinityInterval(row), "—");
  }
});
