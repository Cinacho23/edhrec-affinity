import assert from "node:assert/strict";
import test from "node:test";

import {
  BUILD_DISCOVERY_SORT_OPTIONS, DEFAULT_BUILD_DISCOVERY_FILTERS,
  buildDiscoverySearchParams, filterBuildDiscoveryCommanders, getBuildDiscoveryFilterErrors, readBuildDiscoveryFilters,
} from "./buildDiscoveryFilters.js";

const NOW = Date.parse("2026-10-24T12:00:00Z");
const RECENT = "2026-10-24T10:00:00Z";
const OLD = "2026-10-18T10:00:00Z";

function collection({ attempted = 200, frame = 200, complete = true, oldest = RECENT, newest = RECENT, undated = 0 } = {}) {
  return {
    frame_decks: frame, previews_attempted: attempted, complete, target_mode: "all", unavailable_previews: 0,
    freshness: {
      oldest_source_at: oldest, newest_source_at: newest,
      observed_previews: attempted, dated_previews: attempted - undated, undated_previews: undated, stale_previews: 0,
    },
  };
}

function report(slug, extras = {}) {
  return {
    commander_slug: slug, commander_name: slug, status: "fitted", package_count: 3,
    unique_decks: 200, total_edhrec_decks: 250, profile_count: 4, generated_at: RECENT,
    collection: collection(), ...extras,
  };
}

function slugs(reports, filters, now = NOW) {
  return filterBuildDiscoveryCommanders(reports, filters, now).map((entry) => entry.commander_slug);
}

test("default ranks most Auto packages first, then analyzed decks and deterministic names", () => {
  const reports = [
    report("z", { package_count: 2, unique_decks: 5000 }),
    report("b", { unique_decks: 500 }), report("a", { unique_decks: 500 }),
    report("c", { unique_decks: 600 }), report("d", { package_count: 8, unique_decks: 150 }),
  ];
  assert.equal(DEFAULT_BUILD_DISCOVERY_FILTERS.sortBy, "package_count");
  assert.equal(DEFAULT_BUILD_DISCOVERY_FILTERS.direction, "desc");
  assert.deepEqual(slugs(reports), ["d", "c", "a", "b", "z"]);
  assert.deepEqual(slugs(reports, { direction: "asc" }), ["z", "c", "a", "b", "d"]);
});

test("word search ignores case and accents and matches name and slug together", () => {
  const reports = [report("commander-hope", { commander_name: "Dánitha, Benalia’s Hope" }), report("danitha-new-benalia")];
  assert.deepEqual(slugs(reports, { query: "  DANITHA   HÓPE " }), ["commander-hope"]);
  assert.deepEqual(slugs(reports, { query: "commander danitha" }), ["commander-hope"]);
  for (const query of ["Benalia's hope", "Benalia’s hope", "benalias hope"]) {
    assert.deepEqual(slugs(reports, { query }), ["commander-hope"]);
  }
  assert.deepEqual(slugs(reports, { query: "hope missing" }), []);
});

test("combines inclusive count ranges and search without imposing the eligibility threshold", () => {
  const reports = [
    report("match", { package_count: 4, unique_decks: 300, total_edhrec_decks: 450, profile_count: 7 }),
    report("too-many-packages", { commander_name: "Match extra packages", package_count: 5, unique_decks: 300, total_edhrec_decks: 450, profile_count: 7 }),
    report("too-few-analyzed", { commander_name: "Match fewer analyzed", package_count: 4, unique_decks: 299, total_edhrec_decks: 450, profile_count: 7 }),
    report("too-many-total", { commander_name: "Match larger population", package_count: 4, unique_decks: 300, total_edhrec_decks: 451, profile_count: 7 }),
    report("too-few-profiles", { commander_name: "Match fewer profiles", package_count: 4, unique_decks: 300, total_edhrec_decks: 450, profile_count: 6 }),
    report("small-population", { total_edhrec_decks: 15 }),
  ];
  assert.deepEqual(slugs(reports, {
    query: "match", minPackages: "4", maxPackages: "4", minAnalyzed: "300", maxAnalyzed: "300",
    minTotalDecks: "450", maxTotalDecks: "450", minProfiles: "7", maxProfiles: "7",
  }), ["match"]);
  assert.deepEqual(slugs(reports, { maxTotalDecks: "15" }), ["small-population"]);
  assert.equal(slugs(reports).length, 6);
});

test("zero is an exact filterable count; missing and invalid report values do not become zero", () => {
  const reports = [report("zero", { package_count: 0 }), ...[undefined, null, "0", NaN, -1, 0.5, Infinity]
    .map((value, index) => report(`invalid-${index}`, { package_count: value }))];
  assert.deepEqual(slugs(reports, { minPackages: "0", maxPackages: "0" }), ["zero"]);
  assert.equal(slugs(reports).length, reports.length);
  for (const [field, filter] of [["unique_decks", "minAnalyzed"], ["profile_count", "minProfiles"], ["total_edhrec_decks", "minTotalDecks"]]) {
    assert.deepEqual(slugs([report("missing", { [field]: null })], { [filter]: "0" }), []);
  }
});

test("validates whole number inputs, safe integer limits, and every inverted range", () => {
  for (const input of ["-1", "1.5", "1e3", "+2", "abc", "9007199254740992", NaN, Infinity, {}, true]) {
    assert.equal(getBuildDiscoveryFilterErrors({ minPackages: input }).length, 1, String(input));
    assert.deepEqual(slugs([report("a")], { minPackages: input }), []);
  }
  assert.deepEqual(getBuildDiscoveryFilterErrors({ minPackages: " 0 ", maxPackages: "9007199254740991", minAnalyzed: 3 }), []);
  assert.deepEqual(getBuildDiscoveryFilterErrors({ minPackages: " ", maxPackages: null }), []);
  const errors = getBuildDiscoveryFilterErrors({
    minPackages: "4", maxPackages: "3", minAnalyzed: "3", maxAnalyzed: "2",
    minTotalDecks: "2", maxTotalDecks: "1", minProfiles: "1", maxProfiles: "0",
  });
  assert.equal(errors.length, 4);
  assert.ok(errors.every((error) => error.includes("minimum cannot exceed its maximum")));
  assert.deepEqual(slugs([report("a")], { minAnalyzed: "3", maxAnalyzed: "2" }), []);
});

test("complete coverage stays separate from source freshness and partial analysis", () => {
  const reports = [
    report("complete-stale", { collection: collection({ oldest: OLD, newest: OLD }) }),
    report("partial-fresh", { collection: collection({ attempted: 100, complete: false }) }),
    report("unknown", { collection: undefined }),
  ];
  assert.deepEqual(slugs(reports, { coverage: "complete" }), ["complete-stale"]);
  assert.deepEqual(slugs(reports, { coverage: "partial" }), ["partial-fresh"]);
  assert.deepEqual(slugs(reports, { coverage: "unknown" }), ["unknown"]);
  assert.deepEqual(slugs(reports, { coverage: "complete", freshness: "fresh" }), []);
});

test("freshness filter joins stale and mixed evidence and uses the supplied clock", () => {
  const reports = [
    report("fresh"), report("stale", { collection: collection({ oldest: OLD, newest: OLD }) }),
    report("mixed", { collection: collection({ oldest: OLD }) }), report("unknown", { collection: {} }),
  ];
  assert.deepEqual(slugs(reports, { freshness: "fresh" }), ["fresh"]);
  assert.deepEqual(slugs(reports, { freshness: "needs_refresh" }), ["mixed", "stale"]);
  assert.deepEqual(slugs(reports, { freshness: "unknown" }), ["unknown"]);
  assert.deepEqual(slugs(reports, { freshness: "fresh" }, NOW + 5 * 24 * 60 * 60 * 1000), []);
});

test("analysis status can combine with coverage, freshness, and counts", () => {
  const reports = [report("fitted"), report("insufficient", { status: "insufficient_data", package_count: 0 })];
  assert.deepEqual(slugs(reports, { status: "insufficient_data", coverage: "complete", freshness: "fresh", maxPackages: "0" }), ["insufficient"]);
  assert.deepEqual(slugs(reports, { status: "fitted" }), ["fitted"]);
});

test("invalid enum values safely fall back to unrestricted filters and default ordering", () => {
  const reports = [report("a", { package_count: 1 }), report("b", { package_count: 6 })];
  assert.deepEqual(slugs(reports, { status: "invented", coverage: "invented", freshness: "invented", sortBy: "invented", direction: "invented" }), ["b", "a"]);
});

test("all numeric sort options keep invalid values last in both directions", () => {
  for (const sortBy of ["package_count", "unique_decks", "total_edhrec_decks", "profile_count"]) {
    const reports = [report("unknown", { [sortBy]: null }), report("low", { [sortBy]: 0 }), report("high", { [sortBy]: 10 })];
    assert.deepEqual(slugs(reports, { sortBy, direction: "asc" }), ["low", "high", "unknown"], sortBy);
    assert.deepEqual(slugs(reports, { sortBy, direction: "desc" }), ["high", "low", "unknown"], sortBy);
  }
});

test("coverage ordering uses attempted / frame and handles a completed empty index", () => {
  const reports = [
    report("unknown", { collection: {} }),
    report("quarter", { collection: collection({ attempted: 100, frame: 400, complete: false }) }),
    report("half", { collection: collection({ attempted: 200, frame: 400, complete: false }) }),
    report("complete-empty", { collection: collection({ attempted: 0, frame: 0 }) }),
    report("empty-not-complete", { collection: collection({ attempted: 0, frame: 0, complete: false }) }),
  ];
  assert.deepEqual(slugs(reports, { sortBy: "coverage", direction: "desc" }), ["complete-empty", "half", "quarter", "empty-not-complete", "unknown"]);
  assert.deepEqual(slugs(reports, { sortBy: "coverage", direction: "asc" }), ["quarter", "half", "complete-empty", "empty-not-complete", "unknown"]);
  assert.deepEqual(slugs(reports, { coverage: "complete" }), ["complete-empty"]);
});

test("analysis dates sort chronologically with absent and invalid dates last", () => {
  const reports = [
    report("missing", { generated_at: null }), report("invalid", { generated_at: "not-a-date" }),
    report("older", { generated_at: OLD }), report("newer", { generated_at: RECENT }),
  ];
  assert.deepEqual(slugs(reports, { sortBy: "generated_at", direction: "desc" }), ["newer", "older", "invalid", "missing"]);
  assert.deepEqual(slugs(reports, { sortBy: "generated_at", direction: "asc" }), ["older", "newer", "invalid", "missing"]);
});

test("name ordering uses slug fallback and breaks equal names by slug", () => {
  const reports = [report("z", { commander_name: "Same" }), report("a", { commander_name: "Same" }), report("before", { commander_name: "" })];
  assert.deepEqual(slugs(reports, { sortBy: "commander_name", direction: "asc" }), ["before", "a", "z"]);
  assert.deepEqual(slugs(reports, { sortBy: "commander_name", direction: "desc" }), ["a", "z", "before"]);
});

test("filtering and sorting preserve source records, collections, array order, and filter inputs", () => {
  const reports = [report("a", { package_count: 1 }), report("b", { package_count: 6 })];
  const filters = { minPackages: "0", sortBy: "package_count" };
  const original = structuredClone({ reports, filters });
  Object.freeze(reports);
  const filtered = filterBuildDiscoveryCommanders(reports, filters, NOW);
  assert.deepEqual(filtered.map((entry) => entry.commander_slug), ["b", "a"]);
  assert.equal(filtered[0], reports[1]);
  assert.deepEqual({ reports, filters }, original);
});

test("exports all supported sort choices and handles absent inputs", () => {
  assert.deepEqual(BUILD_DISCOVERY_SORT_OPTIONS.map((option) => option.value), [
    "package_count", "unique_decks", "total_edhrec_decks", "profile_count", "coverage", "generated_at", "commander_name",
  ]);
  assert.ok(BUILD_DISCOVERY_SORT_OPTIONS.every((option) => option.label));
  assert.deepEqual(filterBuildDiscoveryCommanders(undefined), []);
  assert.deepEqual(getBuildDiscoveryFilterErrors(null), []);
});

test("URL filters round trip count ranges, accented search, statuses and sorting without unknown fields", () => {
  const filters = {
    ...DEFAULT_BUILD_DISCOVERY_FILTERS,
    query: "Dánitha & counters", minPackages: "2", maxPackages: "8", minAnalyzed: "200", maxAnalyzed: "3000",
    minTotalDecks: "250", maxTotalDecks: "10000", minProfiles: "0", maxProfiles: "12",
    status: "fitted", coverage: "complete", freshness: "needs_refresh", sortBy: "unique_decks", direction: "asc",
  };
  const params = buildDiscoverySearchParams({ ...filters, unrecognized: "ignored" });
  assert.equal(params.has("unrecognized"), false);
  assert.deepEqual(readBuildDiscoveryFilters(new URLSearchParams(params.toString())), filters);
  assert.equal(buildDiscoverySearchParams(DEFAULT_BUILD_DISCOVERY_FILTERS).toString(), "");
  assert.deepEqual(readBuildDiscoveryFilters(), DEFAULT_BUILD_DISCOVERY_FILTERS);
});

test("URL helpers default invalid enum values but retain invalid count text for visible errors", () => {
  const filters = readBuildDiscoveryFilters(new URLSearchParams("status=bad&coverage=bad&freshness=bad&sortBy=bad&direction=bad&minAnalyzed=abc&maxPackages=-1&secret=ignored"));
  assert.equal(filters.status, "all");
  assert.equal(filters.coverage, "all");
  assert.equal(filters.freshness, "all");
  assert.equal(filters.sortBy, "package_count");
  assert.equal(filters.direction, "desc");
  assert.equal(filters.minAnalyzed, "abc");
  assert.equal(filters.maxPackages, "-1");
  assert.equal("secret" in filters, false);
  assert.equal(getBuildDiscoveryFilterErrors(filters).length, 2);
  assert.equal(buildDiscoverySearchParams({ ...filters, status: "bad", direction: "bad" }).toString(), "maxPackages=-1&minAnalyzed=abc");
});
