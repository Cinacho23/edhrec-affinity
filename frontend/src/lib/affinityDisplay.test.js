import assert from "node:assert/strict";
import test from "node:test";

import {
  formatAdjustedAffinity,
  formatAffinityInterval,
  formatAffinityProbability,
  formatBuildRarity,
  getBuildRarity,
  getDecisionAffinityMetrics,
  getThemeAffinityMetrics,
} from "./affinityDisplay.js";

test("affinity display treats model fields as probabilities, including zero and one", () => {
  assert.equal(formatAffinityProbability(0), "0.00%");
  assert.equal(formatAffinityProbability(1), "100.00%");
  assert.equal(formatAffinityProbability("0.2"), "20.00%");
  for (const missing of [null, undefined, "", " ", false, [], NaN, Infinity, -0.1, 20]) {
    assert.equal(formatAffinityProbability(missing), "—");
  }
});

test("build rarity describes within-commander frequency independently of specialization z", () => {
  for (const [affinity, rarity] of [[0.2, 5], [0.02, 50], [0.002, 500], [1, 1]]) {
    const row = { tag_affinity_adjusted_pct: affinity, affinity_model_status: "fitted", z: 4 };
    assert.equal(getBuildRarity(row), rarity);
    assert.equal(formatBuildRarity(row), `≈1 in ${rarity}`);
    assert.equal(getBuildRarity({ ...row, z: -0.5 }), rarity);
  }
  assert.equal(formatBuildRarity({ tag_affinity_adjusted_pct: "0.0581651449" }), "≈1 in 17.2");
});

test("build rarity uses the adjusted estimate and labels explicit unadjusted fallbacks", () => {
  assert.equal(getBuildRarity({ tag_affinity_pct: 0.01, tag_affinity_adjusted_pct: 0.02 }), 50);
  assert.equal(formatBuildRarity({ tag_affinity_adjusted_pct: 0.02, affinity_model_status: "fallback_sparse" }), "≈1 in 50 (unadjusted)");
  assert.equal(getBuildRarity({ tag_affinity_pct: 0.02 }), null);
  for (const value of [null, undefined, "", " ", false, [], NaN, Infinity, -0.1, 20, 0, Number.MIN_VALUE]) {
    assert.equal(getBuildRarity({ tag_affinity_adjusted_pct: value }), null);
    assert.equal(formatBuildRarity({ tag_affinity_adjusted_pct: value }), "—");
  }
});

test("build rarity for theme and bracket rows uses the displayed tag without borrowing another tag", () => {
  const row = {
    tag_affinity_adjusted_pct: 0.9,
    theme_affinity_adjusted_pct: 0.02,
    theme_affinity_model_status: "fitted",
    decision_tag_affinity_adjusted_pct: 0.2,
    decision_affinity_model_status: "fitted",
  };
  assert.equal(formatBuildRarity(getThemeAffinityMetrics(row)), "≈1 in 50");
  assert.equal(formatBuildRarity(getDecisionAffinityMetrics(row)), "≈1 in 5");
  assert.equal(getBuildRarity(getThemeAffinityMetrics({ ...row, theme_affinity_adjusted_pct: null })), null);
});

test("deciding-tag adapters keep every displayed metric on the deciding tag", () => {
  const metrics = getDecisionAffinityMetrics({
    decision_tag_affinity_pct: "0.2",
    decision_tag_affinity_adjusted_pct: 0.15,
    decision_tag_affinity_lower_pct: 0.1,
    decision_tag_affinity_upper_pct: 0.22,
    decision_affinity_model_status: "fitted",
    decision_affinity_model_version: "beta_binomial_v1",
    decision_affinity_z: "0.5",
    decision_rank_within_tag_by_z: "17",
    decision_z: 3,
    tag_affinity_pct: 0.99,
    z: 9,
  });
  assert.equal(formatAffinityProbability(metrics.tag_affinity_pct), "20.00%");
  assert.equal(formatAdjustedAffinity(metrics), "15.00%");
  assert.equal(formatAffinityInterval(metrics), "10.00%–22.00%");
  assert.equal(metrics.z, 0.5);
  assert.equal(metrics.rank_within_tag_by_z, 17);
  assert.equal(metrics.affinity_model_version, "beta_binomial_v1");
});

test("theme adapters retain fallback labels and do not borrow deciding-tag values", () => {
  const metrics = getThemeAffinityMetrics({
    theme_affinity_pct: 0.2,
    theme_affinity_adjusted_pct: 0.2,
    theme_affinity_lower_pct: null,
    theme_affinity_upper_pct: null,
    theme_affinity_model_status: "fallback_sparse",
    theme_affinity_z: -0.5,
    theme_rank_within_tag_by_z: 8,
    decision_affinity_z: 9,
    decision_tag_affinity_pct: 0.99,
  });
  assert.equal(formatAdjustedAffinity(metrics), "20.00% (unadjusted)");
  assert.equal(formatAffinityInterval(metrics), "—");
  assert.equal(metrics.z, -0.5);
  assert.equal(metrics.rank_within_tag_by_z, 8);
});

test("prefixed adapters preserve absent and explicit-null upgraded scores rather than using legacy scores", () => {
  for (const [adapt, scoreKey, rankKey, rawKey] of [
    [getDecisionAffinityMetrics, "decision_affinity_z", "decision_rank_within_tag_by_z", "decision_tag_affinity_pct"],
    [getThemeAffinityMetrics, "theme_affinity_z", "theme_rank_within_tag_by_z", "theme_affinity_pct"],
  ]) {
    for (const missing of [null, undefined, "", " ", false, NaN, Infinity]) {
      const metrics = adapt({ [scoreKey]: missing, [rankKey]: missing, [rawKey]: missing, decision_z: 4, theme_z: 5, z: 6 });
      assert.equal(metrics.z, null);
      assert.equal(metrics.rank_within_tag_by_z, null);
      assert.equal(formatAffinityProbability(metrics.tag_affinity_pct), "—");
    }
    assert.equal(adapt({ [scoreKey]: 0 }).z, 0);
    assert.equal(adapt({ [rankKey]: 0 }).rank_within_tag_by_z, null);
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
