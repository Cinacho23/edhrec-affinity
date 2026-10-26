import assert from "node:assert/strict";
import test from "node:test";

import {
  formatBuildLift, formatBuildPoints, formatBuildShare, generatedDeckFilename, getBuildCollectionProgress, getBuildModel, getBuildModelOptions, getBuildSourceFreshness, getBuildThemeEvidence, getComparableProfilePairs,
  getCurrentBuildDiscoveryCommanders, getDeckMixtureRows, getExternalDeckRating, getGeneratedDeckColorChoices, getGeneratedDeckDistinctiveCards, getGeneratedDeckExport, getGeneratedDeckGroups, getPackageBracketLabel, getSupportedThemes, hasBuildStylePopulation, isBuildMetadataFresh, safeExternalUrl, splitPrimerCardReferences,
} from "./buildStyleUtils.js";

test("discovery lists only explicitly approved current collector reports", () => {
  const commanders = [
    { commander_slug: "old-fitted", total_edhrec_decks: 5000, status: "fitted", generated_at: "2026-10-24T00:00:00Z" },
    { commander_slug: "new-insufficient", total_edhrec_decks: 200, unique_decks: 8, status: "insufficient_data", generated_at: "2026-10-01T00:00:00Z" },
    { commander_slug: "new-fitted", total_edhrec_decks: 1000, status: "fitted" },
  ];
  const index = { commanders, discovery: { policy: "current_collector", min_total_decks: 200, commander_slugs: ["new-fitted", "new-insufficient", "missing"] } };
  assert.deepEqual(getCurrentBuildDiscoveryCommanders(index), commanders.slice(1));
  assert.equal(index.commanders.length, 3);
  assert.equal(getCurrentBuildDiscoveryCommanders(index)[0], commanders[1]);
  for (const invalid of [null, { commanders }, { ...index, discovery: { commander_slugs: ["old-fitted"] } },
    { ...index, discovery: { policy: "legacy", commander_slugs: ["old-fitted"] } },
    { ...index, discovery: { policy: "current_collector", commander_slugs: null } }]) {
    assert.deepEqual(getCurrentBuildDiscoveryCommanders(invalid), []);
  }
});

test("current collector membership grows beyond its baseline without limiting or duplicating cards", () => {
  const commanders = Array.from({ length: 80 }, (_, i) => ({ commander_slug: `current-${i}`, total_edhrec_decks: 200 + i }));
  const approved = commanders.map((entry) => entry.commander_slug);
  const index = { commanders: [...commanders, commanders[0]], discovery: {
    policy: "current_collector", min_total_decks: 200, baseline_report_count: 25, commander_slugs: [...approved, approved[0], null, 8],
  } };
  assert.deepEqual(getCurrentBuildDiscoveryCommanders(index), commanders);
  index.discovery.commander_slugs = [];
  assert.deepEqual(getCurrentBuildDiscoveryCommanders(index), []);
});

test("fresh discovery requires the commander population threshold rather than its analyzed sample size", () => {
  const commanders = [
    { commander_slug: "small", total_edhrec_decks: 199, unique_decks: 199 },
    { commander_slug: "enough", total_edhrec_decks: 200, unique_decks: 8 },
    { commander_slug: "unknown", unique_decks: 500 },
    { commander_slug: "invalid", total_edhrec_decks: "500", unique_decks: 500 },
  ];
  const index = { commanders, discovery: { policy: "current_collector", min_total_decks: 200,
    commander_slugs: commanders.map((entry) => entry.commander_slug) } };
  assert.deepEqual(getCurrentBuildDiscoveryCommanders(index), [commanders[1]]);
  delete index.discovery.min_total_decks;
  assert.deepEqual(getCurrentBuildDiscoveryCommanders(index), []);
  index.discovery.min_total_decks = 300;
  assert.deepEqual(getCurrentBuildDiscoveryCommanders(index), []);
});

test("native build reports require a known population of at least 200 before packages can be shown", () => {
  for (const count of [undefined, null, 0, 6, 199, "200", NaN, Infinity, 200.5]) {
    assert.equal(hasBuildStylePopulation({ total_edhrec_decks: count,
      deck_analysis: { unique_decks: 500, packages: [{ id: 0, generated_deck: { status: "generated" } }] } }), false);
  }
  assert.equal(hasBuildStylePopulation(null), false);
  assert.equal(hasBuildStylePopulation({ total_edhrec_decks: 200, deck_analysis: { unique_decks: 8 } }), true);
  assert.equal(hasBuildStylePopulation({ total_edhrec_decks: 500 }, 600), false);
  assert.equal(hasBuildStylePopulation({ total_edhrec_decks: 500 }, 199), false);
});

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

test("tag evidence distinguishes main and supporting profiles without treating deck count as package membership", () => {
  const main = getBuildThemeEvidence({ support_tier: "main", deck_count: 15,
    commander_deck_count: 300, deck_fraction: 0.05, similarity: 0.4 });
  assert.equal(main.tier, "main");
  assert.equal(main.note, "Main · 5%");
  assert.match(main.tooltip, /EDHREC lists 15 tag decks out of 300 total decks for this commander \(5%\)/);
  assert.match(main.tooltip, /Card-profile similarity: 0.40/);
  assert.match(main.tooltip, /not model confidence or package membership/);
  assert.match(main.tooltip, /not confirmed tags on every analyzed deck/);
  assert.match(main.tooltip, /Tags can overlap, so percentages need not sum to 100%/);
  assert.doesNotMatch(main.tooltip, /tentative/);
  const supporting = getBuildThemeEvidence({ support_tier: "supporting", deck_count: 5,
    commander_deck_count: 300, deck_fraction: 5 / 300, similarity: 0.71 });
  assert.equal(supporting.tier, "supporting");
  assert.equal(supporting.note, "Supporting · 1.7%");
  assert.match(supporting.tooltip, /EDHREC lists 5 tag decks out of 300 total decks/);
  assert.match(supporting.tooltip, /Supporting matches may have lower prevalence or less source evidence/);
  assert.match(supporting.tooltip, /Tiers use reported tag prevalence and minimum deck-count evidence/);
  const largeSupporting = getBuildThemeEvidence({ support_tier: "supporting", deck_count: 1000,
    commander_deck_count: 100000, deck_fraction: 0.01 });
  assert.equal(largeSupporting.note, "Supporting · 1%");
  assert.doesNotMatch(largeSupporting.tooltip, /[Ss]mall sample/);
  assert.equal(getBuildThemeEvidence({ support_tier: "supporting", deck_count: 9,
    commander_deck_count: 220, deck_fraction: 9 / 220 }).note, "Supporting · 4.1%");
  assert.equal(getBuildThemeEvidence({ support_tier: "main", deck_count: 1234 }).note, "Main · 1,234 tag decks");
});

test("tag percentages use their explicit commander population rather than analyzed sample counts", () => {
  const evidence = { support_tier: "main", deck_count: 500, commander_deck_count: 10000,
    deck_fraction: 0.05, unique_decks: 60, member_count: 15 };
  assert.equal(getBuildThemeEvidence(evidence).note, "Main · 5%");
  assert.match(getBuildThemeEvidence(evidence).tooltip, /500 tag decks out of 10,000 total decks/);
  assert.equal(getBuildThemeEvidence({ ...evidence, unique_decks: 5000, member_count: 2000 }).note, "Main · 5%");
  assert.equal(getBuildThemeEvidence({ deck_count: 500, commander_deck_count: 10000, deck_fraction: 0.05 }).note, "5%");
});

test("legacy and malformed population evidence falls back to raw counts without inventing a percentage", () => {
  const theme = { support_tier: "supporting", deck_count: 5, commander_deck_count: 500, deck_fraction: 0.01 };
  for (const invalid of [
    { commander_deck_count: undefined }, { commander_deck_count: null }, { commander_deck_count: 0 },
    { commander_deck_count: "500" }, { commander_deck_count: 1.5 }, { commander_deck_count: 4 },
    { deck_fraction: undefined }, { deck_fraction: null }, { deck_fraction: "0.01" },
    { deck_fraction: NaN }, { deck_fraction: Infinity }, { deck_fraction: -0.01 },
    { deck_fraction: 1.01 }, { deck_fraction: 0.5 },
  ]) {
    const result = getBuildThemeEvidence({ ...theme, ...invalid, unique_decks: 500 });
    assert.equal(result.note, "Supporting · 5 tag decks");
    assert.match(result.tooltip, /Commander-wide tag percentage unavailable/);
    assert.doesNotMatch(result.note, /%|NaN|Infinity/);
  }
  assert.equal(getBuildThemeEvidence({ ...theme, deck_fraction: 0.0100001 }).note, "Supporting · 1%");
});

test("legacy or malformed tag evidence never invents a support tier or deck count", () => {
  for (const theme of [undefined, null, {}, { support_tier: "unexpected" }, { deck_count: null },
    { deck_count: "20" }, { deck_count: NaN }, { deck_count: Infinity }, { deck_count: -1 }, { deck_count: 5.5 }]) {
    const result = getBuildThemeEvidence(theme);
    assert.equal(result.tier, null);
    assert.equal(result.note, "");
    assert.match(result.tooltip, /deck count unavailable/);
    assert.doesNotMatch(result.tooltip, /Main tag|Supporting tag|NaN|Infinity/);
  }
  assert.equal(getBuildThemeEvidence({ deck_count: 25 }).note, "25 tag decks");
  assert.equal(getBuildThemeEvidence({ deck_count: 5 }).tier, null);
  assert.equal(getBuildThemeEvidence({ support_tier: "supporting" }).note, "Supporting");
  assert.match(getBuildThemeEvidence({ support_tier: "supporting" }).tooltip, /deck count unavailable/);
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

test("distinctive-card coverage preserves explicit omissions and their explanations", () => {
  const deck = { status: "generated", generation_version: 2, distinctive_cards: {
    total: 3, included_count: 2, coverage: 2 / 3,
    included: [{ name: "Core card" }, { name: "Alternate name", canonical_name: "Rules name" }],
    omitted: [{ name: "Third card", reason_code: "color_identity", reason: "Outside the commander’s color identity." }],
  } };
  assert.deepEqual(getGeneratedDeckDistinctiveCards(deck), {
    total: 3, includedCount: 2, omitted: deck.distinctive_cards.omitted,
  });
  const result = getGeneratedDeckDistinctiveCards(deck);
  assert.notEqual(result.omitted, deck.distinctive_cards.omitted);
  assert.notEqual(result.omitted[0], deck.distinctive_cards.omitted[0]);
  deck.distinctive_cards.omitted[0].reason = "";
  assert.equal(getGeneratedDeckDistinctiveCards(deck).omitted[0].reason,
    "The saved report does not include an omission explanation.");
  deck.distinctive_cards.included.push({ name: "Third card" });
  deck.distinctive_cards.included_count = 3;
  deck.distinctive_cards.omitted = [];
  assert.deepEqual(getGeneratedDeckDistinctiveCards(deck), { total: 3, includedCount: 3, omitted: [] });
});

test("legacy, unavailable, and inconsistent deck evidence never claims distinctive-card coverage", () => {
  const evidence = { total: 1, included_count: 1, included: [{ name: "Core card" }], omitted: [] };
  for (const deck of [null, {}, { status: "generated" },
    { status: "unavailable", generation_version: 2, distinctive_cards: evidence },
    { status: "generated", generation_version: 1, distinctive_cards: evidence },
    ...[
      { ...evidence, included_count: 0 }, { ...evidence, total: 2 },
      { ...evidence, total: "1" }, { ...evidence, included: [{ name: "" }] },
      { ...evidence, omitted: null },
    ].map((distinctive_cards) => ({ status: "generated", generation_version: 2, distinctive_cards })),
  ]) assert.equal(getGeneratedDeckDistinctiveCards(deck), null);
});

test("generated decks explain only valid choices for their own commanders", () => {
  const deck = { status: "generated", commanders: [{ name: "Clara Oswald" }], validation: {} };
  assert.deepEqual(getGeneratedDeckColorChoices(deck), []);
  for (const [color, colorName] of Object.entries({ W: "White", U: "Blue", B: "Black", R: "Red", G: "Green" })) {
    deck.validation.commander_color_choices = [{ commander_name: "Clara Oswald", color }];
    assert.deepEqual(getGeneratedDeckColorChoices(deck), [{ commanderName: "Clara Oswald", colorName }]);
  }
  for (const choices of [null, [{ commander_name: "Clara Oswald", color: "C" }],
    [{ commander_name: "Other commander", color: "R" }],
    [{ commander_name: "Clara Oswald", color: "R" }, { commander_name: "Clara Oswald", color: "G" }],
    [null],
  ]) {
    deck.validation.commander_color_choices = choices;
    assert.deepEqual(getGeneratedDeckColorChoices(deck), []);
  }
});

test("legacy decks display and export plain card lists without category tags", () => {
  const deck = { commanders: [{ name: "Commander A", quantity: 1 }], export_text: "1 Hybrid spell #Card Draw\n", cards: [
    { name: "Hybrid spell", quantity: 1, primary_role: "Card Draw", tags: ["Card Draw", "Tokens", "+1/+1 Counters"] },
    { name: "Forest", quantity: 35, tags: ["Lands"] },
    { name: "Commander A", quantity: 1, primary_role: "Tokens", tags: ["Commander", "Tokens"] },
    { name: "Unknown role", quantity: 1, tags: ["Theme A", "Theme B"] },
    { name: "Invalid", quantity: 0, tags: ["Ramp"] },
  ] };
  const groups = getGeneratedDeckGroups(deck);
  assert.deepEqual(groups.map(({ name, count }) => [name, count]), [
    ["Commander", 1], ["Main deck", 37],
  ]);
  assert.ok(groups.flatMap((group) => group.cards).every((card) => !("tags" in card)));
  const exported = getGeneratedDeckExport(deck);
  assert.equal(exported, "1 Commander A *CMDR*\n35 Forest\n1 Hybrid spell\n1 Unknown role\n");
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

test("generated deck sections use commanders and alphabetical main deck cards", () => {
  const roles = ["Commander", "Lands", "Fast Mana", "Ramp", "Card Advantage", "Wipe", "Removal", "Tutor", "Recursion", "Protection", "Leftover"];
  const cards = roles.toReversed().map((primary_role) => ({ name: `${primary_role} card`, quantity: 1, primary_role, tags: [primary_role, "Theme A"] }));
  const groups = getGeneratedDeckGroups({ cards });
  assert.deepEqual(groups.map((group) => group.name), ["Commander", "Main deck"]);
  assert.deepEqual(groups[1].cards.map((card) => card.name), roles.slice(1).map((role) => `${role} card`).toSorted());
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
