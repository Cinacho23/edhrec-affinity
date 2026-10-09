import assert from "node:assert/strict";
import test from "node:test";
import { buildCommanderThemeBracketRows } from "./bracketUtils.js";
import { createThemeBracketDataLoader, mapWithConcurrency } from "./themeBracketData.js";

function tag(commanderSlug, tagSlug, z) {
  return {
    commander_slug: commanderSlug,
    commander_name: commanderSlug,
    tag_slug: tagSlug,
    tag_name: tagSlug,
    z,
    tag_decks: 10,
    total_decks: 200,
  };
}

function createSource(files = {}) {
  const calls = [];
  let active = 0;
  let maxActive = 0;
  const methods = [
    "loadThemeBracketIndex", "loadThemeBracketDetail", "loadTagIndex",
    "loadTagDetail", "loadCommanderDetail",
  ];
  const dataSource = Object.fromEntries(methods.map((method) => [method, async (slug = "") => {
    const key = `${method}:${slug}`;
    calls.push(key);
    active += 1;
    maxActive = Math.max(maxActive, active);
    await new Promise((resolve) => setTimeout(resolve, 1));
    active -= 1;
    if (!(key in files)) throw new Error(`Missing ${key}`);
    return files[key];
  }]));
  return { dataSource, calls, get maxActive() { return maxActive; } };
}

function signalFiles() {
  return Object.fromEntries(["cedh", "aggro", "control", "midrange", "tempo", "combo"]
    .map((slug) => [`loadTagDetail:${slug}`, []]));
}

test("reuses compact index and theme requests without loading legacy data", async () => {
  const themes = [{ tag_slug: "tokens", tag_name: "Tokens" }];
  const rows = [{ commander_slug: "alpha", theme_tag_slug: "tokens", theme_z: 3 }];
  const source = createSource({
    "loadThemeBracketIndex:": themes,
    "loadThemeBracketDetail:tokens": rows,
  });
  const loader = createThemeBracketDataLoader({ dataSource: source.dataSource });
  assert.equal(loader.loadIndex(), loader.loadIndex());
  assert.deepEqual(await loader.loadIndex(), { themes, usesThemeBracketFiles: true });
  assert.equal(loader.loadRows("tokens"), loader.loadRows("tokens"));
  assert.equal(await loader.loadRows("tokens"), rows);
  assert.deepEqual(source.calls, ["loadThemeBracketIndex:", "loadThemeBracketDetail:tokens"]);
});

test("legacy themes share six complete signal files and preserve both eligibility gates", async () => {
  const source = createSource({
    ...signalFiles(),
    "loadTagIndex:": [{ tag_slug: "tokens" }, { tag_slug: "mutate" }],
    "loadTagDetail:tokens": [tag("alpha", "tokens", 2), tag("beta", "tokens", 1.04)],
    "loadTagDetail:mutate": [tag("alpha", "mutate", 1.05)],
    "loadTagDetail:cedh": [tag("alpha", "cedh", 0.4)],
    "loadTagDetail:combo": [tag("alpha", "combo", 4), tag("beta", "combo", 0.02)],
  });
  const loader = createThemeBracketDataLoader({ concurrency: 2, dataSource: source.dataSource });
  assert.equal((await loader.loadIndex()).usesThemeBracketFiles, false);
  const [tokens, mutate, combo] = await Promise.all([
    loader.loadRows("tokens", false),
    loader.loadRows("mutate", false),
    loader.loadRows("combo", false),
  ]);
  assert.deepEqual(buildCommanderThemeBracketRows(tokens, "tokens").map((row) => row.bracket_key), ["4"]);
  assert.deepEqual(buildCommanderThemeBracketRows(mutate, "mutate").map((row) => row.bracket_key), ["4"]);
  assert.deepEqual(buildCommanderThemeBracketRows(combo, "combo").map((row) => row.bracket_key), ["4", "1/2"]);
  assert.equal(source.calls.filter((call) => call === "loadTagDetail:combo").length, 1);
  assert.equal(source.calls.some((call) => call.startsWith("loadCommanderDetail")), false);
  assert.ok(source.maxActive <= 2);
});

test("missing compact files use the same legacy rows", async () => {
  const source = createSource({
    ...signalFiles(),
    "loadTagDetail:tokens": [tag("alpha", "tokens", 2)],
    "loadTagDetail:aggro": [tag("alpha", "aggro", 1.05)],
  });
  const loader = createThemeBracketDataLoader({ dataSource: source.dataSource });
  const rows = buildCommanderThemeBracketRows(await loader.loadRows("tokens"), "tokens");
  assert.equal(rows[0].bracket_key, "3");
  assert.equal(rows[0].theme_z, 2);
});

test("legacy ordinary themes retain commander ordering for tied deciding tags", async () => {
  const source = createSource({
    ...signalFiles(),
    "loadTagDetail:tokens": [tag("alpha", "tokens", 2)],
    "loadTagDetail:mutate": [tag("alpha", "mutate", 3)],
    "loadTagDetail:aggro": [tag("alpha", "aggro", 1.2)],
    "loadTagDetail:combo": [tag("alpha", "combo", 1.2)],
    "loadCommanderDetail:alpha": [tag("alpha", "combo", 1.2), tag("alpha", "aggro", 1.2)],
  });
  const loader = createThemeBracketDataLoader({ dataSource: source.dataSource });
  const [tokens, mutate, aggro] = await Promise.all([
    loader.loadRows("tokens", false),
    loader.loadRows("mutate", false),
    loader.loadRows("aggro", false),
  ]);
  assert.equal(buildCommanderThemeBracketRows(tokens, "tokens")[0].decision_tag_slug, "combo");
  assert.equal(buildCommanderThemeBracketRows(mutate, "mutate")[0].decision_tag_slug, "combo");
  assert.equal(buildCommanderThemeBracketRows(aggro, "aggro")[0].decision_tag_slug, "aggro");
  assert.equal(source.calls.filter((call) => call === "loadCommanderDetail:alpha").length, 1);
});

test("failed or malformed signal files cannot silently assign commanders to Bracket 1", async () => {
  for (const failure of [undefined, { invalid: "not rows" }]) {
    const files = {
      ...signalFiles(),
      "loadTagDetail:tokens": [tag("alpha", "tokens", 2)],
    };
    if (failure === undefined) delete files["loadTagDetail:control"];
    else files["loadTagDetail:control"] = failure;
    const source = createSource(files);
    const loader = createThemeBracketDataLoader({ dataSource: source.dataSource });
    await assert.rejects(loader.loadRows("tokens", false), /control/);
  }
});

test("failed commander details for a deciding-tag tie remain a visible data error", async () => {
  const source = createSource({
    ...signalFiles(),
    "loadTagDetail:tokens": [tag("alpha", "tokens", 2)],
    "loadTagDetail:aggro": [tag("alpha", "aggro", 1.2)],
    "loadTagDetail:combo": [tag("alpha", "combo", 1.2)],
  });
  const loader = createThemeBracketDataLoader({ dataSource: source.dataSource });
  await assert.rejects(loader.loadRows("tokens", false), /loadCommanderDetail:alpha/);
});

test("concurrent mapping preserves input order and rejects invalid limits", async () => {
  const result = await mapWithConcurrency([3, 1, 2], 2, async (value) => {
    await new Promise((resolve) => setTimeout(resolve, value));
    return value * 2;
  });
  assert.deepEqual(result, [6, 2, 4]);
  await assert.rejects(mapWithConcurrency([1], 0, (value) => value), /positive integer/);
});

test("fallback loaders retain legacy scores after the affinity upgrade", async () => {
  const source = createSource({
    ...signalFiles(),
    "loadTagDetail:tokens": [
      { ...tag("alpha", "tokens", 0.2), legacy_z: 1.2 },
      { ...tag("beta", "tokens", 5), legacy_z: 1.04 },
    ],
    "loadTagDetail:cedh": [{ ...tag("alpha", "cedh", -1), legacy_z: 0.4 }],
  });
  const loader = createThemeBracketDataLoader({ dataSource: source.dataSource });
  const results = buildCommanderThemeBracketRows(await loader.loadRows("tokens", false), "tokens");
  assert.equal(results.length, 1);
  assert.equal(results[0].bracket_key, "4");
  assert.equal(results[0].decision_z, 0.4);
  assert.equal(results[0].theme_z, 1.2);
});

test("fallback and compact rows retain exact selected-theme and deciding-tag metric identities", async () => {
  const theme = { ...tag("alpha", "tokens", 3), legacy_z: 1.2,
    tag_affinity_pct: .2, tag_affinity_adjusted_pct: .18,
    tag_affinity_lower_pct: .15, tag_affinity_upper_pct: .21,
    rank_within_tag_by_z: 7, affinity_model_status: "fitted",
    affinity_model_version: "beta_binomial_v1" };
  const decision = { ...tag("alpha", "cedh", null), legacy_z: .4,
    tag_affinity_pct: .03, tag_affinity_adjusted_pct: .03,
    tag_affinity_lower_pct: null, tag_affinity_upper_pct: null,
    rank_within_tag_by_z: null, affinity_model_status: "fallback_fit_failed",
    affinity_model_version: "beta_binomial_v1" };
  const compact = { commander_slug: "alpha", commander_name: "alpha", total_decks: 200,
    theme_tag_slug: "tokens", theme_tag_name: "tokens", theme_z: 1.2,
    theme_legacy_z: 1.2, theme_affinity_z: 3, theme_tag_decks: 10,
    theme_affinity_pct: .2, theme_affinity_adjusted_pct: .18,
    theme_affinity_lower_pct: .15, theme_affinity_upper_pct: .21,
    theme_rank_within_tag_by_z: 7, theme_affinity_model_status: "fitted",
    theme_affinity_model_version: "beta_binomial_v1", bracket_tag_rows: [decision] };
  const source = createSource({
    ...signalFiles(),
    "loadTagDetail:tokens": [theme],
    "loadTagDetail:cedh": [decision],
    "loadThemeBracketDetail:tokens": [compact],
  });
  const loader = createThemeBracketDataLoader({ dataSource: source.dataSource });
  const fallbackResult = buildCommanderThemeBracketRows(await loader.loadRows("tokens", false), "tokens")[0];
  const compactResult = buildCommanderThemeBracketRows(await loader.loadRows("tokens", true), "tokens")[0];
  const fullResult = buildCommanderThemeBracketRows([theme, decision], "tokens")[0];
  const metrics = (row) => Object.fromEntries(Object.entries(row).filter(([key]) =>
    key.startsWith("decision_") || key.startsWith("theme_") || key.startsWith("bracket_")
  ));
  assert.deepEqual(metrics(fallbackResult), metrics(compactResult));
  assert.deepEqual(metrics(fallbackResult), metrics(fullResult));
  assert.equal(fallbackResult.theme_affinity_adjusted_pct, .18);
  assert.equal(fallbackResult.decision_tag_affinity_pct, .03);
  assert.equal(fallbackResult.decision_affinity_z, null);
  assert.equal(fallbackResult.decision_tag_affinity_lower_pct, null);
  assert.equal(fallbackResult.decision_affinity_model_status, "fallback_fit_failed");
});
