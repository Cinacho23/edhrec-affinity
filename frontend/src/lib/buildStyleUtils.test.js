import assert from "node:assert/strict";
import test from "node:test";

import {
  formatBuildLift, formatBuildPoints, formatBuildShare, generatedDeckFilename, getBuildModel, getBuildModelOptions, getComparableProfilePairs,
  getDeckMixtureRows, getExternalDeckRating, getGeneratedDeckGroups, getSupportedThemes, isBuildMetadataFresh, safeExternalUrl, splitPrimerCardReferences,
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

test("generated deck grouping counts each card once while retaining multiple role and theme tags", () => {
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
    ["Card Draw", "Tokens", "+1/+1 Counters"]);
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
