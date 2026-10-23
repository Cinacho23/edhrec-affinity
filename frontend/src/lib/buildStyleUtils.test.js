import assert from "node:assert/strict";
import test from "node:test";

import {
  formatBuildLift, formatBuildPoints, formatBuildShare, generatedDeckFilename, getBuildCollectionProgress, getBuildModel, getBuildModelOptions, getBuildSourceFreshness, getComparableProfilePairs,
  getDeckMixtureRows, getExternalDeckRating, getGeneratedDeckExport, getGeneratedDeckGroups, getPackageBracketLabel, getSupportedThemes, isBuildMetadataFresh, safeExternalUrl, splitPrimerCardReferences,
} from "./buildStyleUtils.js";

test("models keep any number of packages and match alternative weights by deck ID", () => {
  for (const count of [1, 3, 7]) {
    const primary = {
      packages: [{ id: 0 }],
      decks: [{ id: "a", source_tags: [] }, { id: "b", source_tags: ["Tokens", "Counters", "Sacrifice"] }],
      alternative_models: [{
        components: count,
        packages: Array.from({ length: count }, (_, id) => ({ id })),
        memberships: [{ id: "b", weights: Array(count).fill(1 / count) }, { id: "a", weights: Array(count).fill(0) }],
      }],
    };
    assert.equal(getBuildModel(primary), primary);
    assert.equal(getBuildModel(primary, "missing"), primary);
    const result = getBuildModel(primary, String(count));
    assert.equal(result.packages.length, count);
    assert.deepEqual(result.decks[0].memberships, Array(count).fill(0));
    assert.deepEqual(result.decks[1].memberships, Array(count).fill(1 / count));
    assert.deepEqual(result.decks[1].source_tags, ["Tokens", "Counters", "Sacrifice"]);
    assert.equal(primary.decks[0].memberships, undefined);
  }
});

test("selector lists only available positive integer package counts in numeric order", () => {
  assert.deepEqual(getBuildModelOptions({ alternative_models: [1, 7, 3, 1, 0, 2.5].map((components) => ({ components, packages: [] })) }), [1, 3, 7]);
  assert.deepEqual(getBuildModelOptions(undefined), []);
});

test("all supported suggested themes survive, including single themes and more than three", () => {
  for (const count of [0, 1, 3, 8]) {
    const themes = Array.from({ length: count }, (_, index) => ({ tag_slug: `theme-${index}`, tag_name: `Theme ${index}` }));
    assert.deepEqual(getSupportedThemes({ tag_suggestions: [...themes, ...themes] }), themes);
  }
  assert.deepEqual(getSupportedThemes({}), []);
});

test("mixture filtering uses the selected package and preserves untagged decks", () => {
  const decks = [
    { id: "a", source_tags: [], memberships: [0.1, 0.2, 0.3, 0.4] },
    { id: "b", source_tags: ["Tokens"], memberships: [0, 0, 0, 1] },
    { id: "c", source_tags: ["A", "B", "C"], memberships: [1, 0, 0, 0] },
  ];
  assert.equal(getDeckMixtureRows({ decks }), decks);
  assert.deepEqual(getDeckMixtureRows({ decks }, "3", 0.2).map((deck) => deck.id), ["b", "a"]);
  assert.deepEqual(decks.map((deck) => deck.id), ["a", "b", "c"]);
});

test("profile comparisons reject unavailable profiles and missing similarities", () => {
  const lower = { left: "a", right: "b", similarity: 0 };
  const higher = { left: "b", right: "a", similarity: 0.8 };
  const profiles = {
    profiles: [{ tag_slug: "a" }, { tag_slug: "b" }],
    pairs: [lower, { left: "a", right: "missing", similarity: 1 }, higher, { left: "a", right: "b", similarity: null }],
  };
  assert.deepEqual(getComparableProfilePairs(profiles), [higher, lower]);
});

test("shares distinguish missing evidence from a measured zero; external links disallow scripts", () => {
  assert.equal(formatBuildShare(null), "—");
  assert.equal(formatBuildShare(0), "0%");
  assert.equal(formatBuildShare(0.1234, 1), "12.3%");
  assert.equal(formatBuildPoints(0.1234), "+12.3 pts");
  assert.equal(formatBuildPoints(null), "—");
  assert.equal(formatBuildLift(3.913), "3.91×");
  assert.equal(formatBuildLift(0), "0.00×");
  assert.equal(formatBuildLift(null), "—");
  assert.equal(formatBuildLift(Infinity), "—");
  assert.equal(safeExternalUrl("javascript:alert(1)"), null);
  assert.equal(safeExternalUrl("data:text/html,test"), null);
  assert.equal(safeExternalUrl("/relative"), null);
  assert.equal(safeExternalUrl("https://edhrec.com/deckpreview/abc"), "https://edhrec.com/deckpreview/abc");
});

test("legacy decks display and export one primary category per card", () => {
  const deck = { commanders: [{ name: "Commander A", quantity: 1 }], cards: [
    { name: "Hybrid spell", quantity: 1, primary_role: "Card Draw", tags: ["Card Draw", "Tokens", "+1/+1 Counters"] },
    { name: "Forest", quantity: 35, tags: ["Lands"] },
    { name: "Commander A", quantity: 1, primary_role: "Tokens", tags: ["Commander", "Tokens"] },
    { name: "Unknown role", quantity: 1, tags: ["Theme A", "Theme B"] },
    { name: "Invalid", quantity: 0, tags: ["Ramp"] },
  ] };
  const groups = getGeneratedDeckGroups(deck);
  assert.deepEqual(groups.map(({ name, count }) => [name, count]), [
    ["Commander", 1], ["Lands", 35], ["Card Draw", 1], ["Other Spells", 1],
  ]);
  assert.deepEqual(groups.find((group) => group.name === "Card Draw").cards[0].tags,
    ["Card Draw"]);
  const exported = getGeneratedDeckExport(deck);
  assert.ok(exported.includes("1 Hybrid spell #Card Draw\n"));
  assert.ok(exported.includes("1 Commander A *CMDR* #Commander\n"));
  assert.ok(exported.trim().split("\n").every((line) => line.split("#").length === 2));
  assert.deepEqual(deck.cards[0].tags, ["Card Draw", "Tokens", "+1/+1 Counters"]);
  assert.equal(groups.reduce((total, group) => total + group.count, 0), 38);
  assert.equal(deck.cards[0].name, "Hybrid spell");
  assert.deepEqual(getGeneratedDeckGroups(null), []);
});

test("primer references preserve full names and treat markup as plain text", () => {
  const primer = "Use [[Ghave, Guru of Spores]] with [[Fire // Ice]].\n<img onerror=alert(1)> [[unfinished";
  assert.deepEqual(splitPrimerCardReferences(primer), [
    { type: "text", text: "Use " }, { type: "card", text: "Ghave, Guru of Spores" },
    { type: "text", text: " with " }, { type: "card", text: "Fire // Ice" },
    { type: "text", text: ".\n<img onerror=alert(1)> [[unfinished" },
  ]);
  assert.deepEqual(splitPrimerCardReferences(null), []);
  assert.equal(generatedDeckFilename("../Ghave’s / Tokens: Deck"), "ghave-s-tokens-deck.txt");
  assert.equal(generatedDeckFilename("", "md"), "generated-deck.md");
});

test("only a current external observation can meet the requested playability range", () => {
  const checked_at = "2026-10-20T12:00:00Z";
  const now = Date.parse("2026-10-20T12:01:00Z");
  for (const playability_pct of [72.5, 74.5, 77.5]) {
    assert.equal(getExternalDeckRating({ status: "verified", checked_at, playability_pct }, now).targetMet, true);
  }
  for (const playability_pct of [72.49, 77.51]) {
    assert.equal(getExternalDeckRating({ status: "verified", checked_at, playability_pct }, now).targetMet, false);
  }
  const stale = getExternalDeckRating({ status: "stale", checked_at, playability_pct: 74.5, bracket: 5, power_level: "10+" }, now);
  assert.equal(stale.inTargetRange, true);
  assert.equal(stale.targetMet, false);
  assert.equal(stale.bracket, 5);
  for (const rating of [null, { status: "unverified", playability_pct: 74.5 },
    { status: "verified", playability_pct: NaN }, { status: "verified", playability_pct: 101 }]) {
    assert.equal(getExternalDeckRating(rating), null);
  }
});

test("static external ratings and card-rule metadata expire at view time", () => {
  const checked_at = "2026-10-20T12:00:00Z";
  const checked = Date.parse(checked_at);
  const week = 7 * 24 * 60 * 60 * 1000;
  const rating = { status: "verified", checked_at, playability_pct: 74.5 };
  assert.equal(getExternalDeckRating(rating, checked + week - 1).status, "verified");
  assert.equal(getExternalDeckRating(rating, checked + week).status, "stale");
  assert.equal(getExternalDeckRating(rating, checked + 90 * week).targetMet, false);
  assert.equal(getExternalDeckRating({ ...rating, checked_at: "invalid" }, checked), null);
  assert.equal(getExternalDeckRating({ ...rating, checked_at: undefined }, checked), null);
  assert.equal(getExternalDeckRating(rating, checked - 5 * 60 * 1000).status, "verified");
  assert.equal(getExternalDeckRating(rating, checked - 5 * 60 * 1000 - 1), null);
  const metadata = { oldest_checked_at: checked_at, stale_cached: 0 };
  assert.equal(isBuildMetadataFresh(metadata, checked + week - 1), true);
  assert.equal(isBuildMetadataFresh(metadata, checked + week), false);
  assert.equal(isBuildMetadataFresh({ ...metadata, stale_cached: 1 }, checked), false);
  assert.equal(isBuildMetadataFresh({}, checked), false);
  assert.equal(isBuildMetadataFresh(metadata, checked - 5 * 60 * 1000 - 1), false);
});

test("generated deck groups preserve the generator's functional role order", () => {
  const roles = ["Commander", "Lands", "Fast Mana", "Ramp", "Card Advantage", "Wipe", "Removal", "Tutor", "Recursion", "Protection", "Leftover"];
  const cards = roles.toReversed().map((primary_role) => ({ name: `${primary_role} card`, quantity: 1, primary_role, tags: [primary_role, "Theme A"] }));
  assert.deepEqual(getGeneratedDeckGroups({ cards }).map((group) => group.name), roles);
});

function sourceCollection(overrides = {}) {
  return { target_mode: "all", complete: true, frame_decks: 100, previews_attempted: 100, unavailable_previews: 0,
    freshness: { target_hours: 96, oldest_source_at: "2026-10-20T12:00:00Z", newest_source_at: "2026-10-20T13:00:00Z",
      dated_previews: 98, undated_previews: 0, observed_previews: 98, stale_previews: 0,
      index_source_at: "2026-10-20T14:00:00Z", index_stale: false, source_complete: true, ...overrides } };
}

test("source age uses actual source observations and expires even when the analysis is rebuilt", () => {
  const now = Date.parse("2026-10-21T12:00:00Z");
  assert.equal(getBuildSourceFreshness({ generated_at: "2026-10-21T12:00:00Z" }, now).status, "unknown");
  assert.equal(getBuildSourceFreshness(sourceCollection(), now).status, "fresh");
  const fourDaysLater = Date.parse("2026-10-24T12:00:00Z");
  assert.equal(getBuildSourceFreshness(sourceCollection(), fourDaysLater).status, "mixed");
  assert.equal(getBuildSourceFreshness(sourceCollection(), fourDaysLater + 3600 * 1000).status, "stale");
  assert.equal(getBuildCollectionProgress(sourceCollection(), fourDaysLater).targetMet, false);
});

test("full-index freshness requires coverage plus current deck and index checks", () => {
  const now = Date.parse("2026-10-21T12:00:00Z");
  assert.equal(getBuildCollectionProgress(sourceCollection(), now).targetMet, true);
  const partial = { ...sourceCollection(), complete: false, previews_attempted: 30 };
  assert.equal(getBuildSourceFreshness(partial, now).status, "fresh");
  assert.equal(getBuildCollectionProgress(partial, now).targetMet, false);
  assert.equal(getBuildCollectionProgress(partial, now).label, "Partial index coverage");
  assert.equal(getBuildCollectionProgress({ ...sourceCollection(), target_mode: "sample" }, now).targetMet, false);
  assert.equal(getBuildCollectionProgress({ ...sourceCollection(), analysis_current: false }, now).targetMet, false);
  assert.equal(getBuildCollectionProgress({ ...sourceCollection(), unavailable_previews: 2 }, now).targetMet, false);
  assert.equal(getBuildCollectionProgress(sourceCollection({ index_source_at: "2026-10-01T00:00:00Z" }), now).targetMet, false);
  assert.equal(getBuildCollectionProgress(sourceCollection({ index_stale: true }), now).targetMet, false);
  assert.equal(getBuildCollectionProgress(sourceCollection({ index_source_at: null }), now).targetMet, false);
  assert.equal(getBuildCollectionProgress({ ...sourceCollection({ observed_previews: 0, dated_previews: 0,
    oldest_source_at: null, newest_source_at: null }), frame_decks: 0, previews_attempted: 0 }, now).targetMet, true);
});

test("undated, changed and invalid preview checks cannot claim verified freshness", () => {
  const now = Date.parse("2026-10-21T12:00:00Z");
  const undated = sourceCollection({ dated_previews: 97, undated_previews: 1, stale_previews: 1 });
  assert.equal(getBuildSourceFreshness(undated, now).status, "mixed");
  assert.equal(getBuildCollectionProgress(undated, now).targetMet, false);
  assert.equal(getBuildSourceFreshness(sourceCollection({ stale_previews: 1 }), now).status, "mixed");
  for (const overrides of [{ oldest_source_at: null }, { newest_source_at: "invalid" },
    { oldest_source_at: "2026-10-25T12:00:00Z" }, { oldest_source_at: "2026-10-20T15:00:00Z" },
    { observed_previews: 100 }, { dated_previews: null }]) {
    assert.equal(getBuildSourceFreshness(sourceCollection(overrides), now).status, "unknown");
    assert.equal(getBuildCollectionProgress(sourceCollection(overrides), now).targetMet, false);
  }
  assert.equal(getBuildCollectionProgress({ ...sourceCollection(), previews_attempted: 101 }, now).known, false);
});

test("version verification can retain cached lists without changing their actual download dates", () => {
  const now = Date.parse("2026-10-21T12:00:00Z");
  const collection = sourceCollection({ basis: "current_index_versions",
    oldest_source_at: "2026-09-01T12:00:00Z", newest_source_at: "2026-09-30T12:00:00Z",
    oldest_verified_at: "2026-10-20T12:00:00Z", newest_verified_at: "2026-10-20T14:00:00Z",
    unchanged_verified: 98 });
  const freshness = getBuildSourceFreshness(collection, now);
  assert.equal(freshness.status, "fresh");
  assert.equal(freshness.label, "Collected deck versions verified recently");
  assert.equal(freshness.oldestSourceAt, "2026-09-01T12:00:00Z");
  assert.equal(getBuildCollectionProgress(collection, now).targetMet, true);
  assert.equal(getBuildSourceFreshness({ ...collection, freshness: { ...collection.freshness, basis: undefined } }, now).status, "stale");
  assert.equal(getBuildSourceFreshness({ ...collection, freshness: { ...collection.freshness, oldest_verified_at: null } }, now).status, "unknown");
  assert.equal(getBuildCollectionProgress(collection, now + 96 * 3600 * 1000).targetMet, false);
});

test("package headings prefer a current external bracket and identify fallback estimates", () => {
  const now = Date.parse("2026-10-21T12:00:00Z");
  const deck = { status: "generated", validation: { bracket: { recommended: 3 },
    external_rating: { status: "verified", checked_at: "2026-10-20T12:00:00Z", playability_pct: 74.5, bracket: 5 } } };
  assert.equal(getPackageBracketLabel(deck, now), "Bracket 5 · EDHPowerLevel");
  assert.equal(getPackageBracketLabel(deck, now + 7 * 24 * 3600 * 1000), "Estimated bracket 3");
  assert.equal(getPackageBracketLabel({ ...deck, validation: { ...deck.validation,
    external_rating: { ...deck.validation.external_rating, status: "stale" } } }, now), "Estimated bracket 3");
  assert.equal(getPackageBracketLabel({ ...deck, status: "unavailable" }, now), null);
  assert.equal(getPackageBracketLabel({ status: "generated", validation: {} }, now), null);
  assert.equal(getPackageBracketLabel({ status: "generated", validation: { bracket: { recommended: 9 } } }, now), null);
});
