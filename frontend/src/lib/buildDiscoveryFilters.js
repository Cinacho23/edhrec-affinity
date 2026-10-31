import { getBuildCollectionProgress, getBuildSourceFreshness } from "./buildStyleUtils.js";

export const DEFAULT_BUILD_DISCOVERY_FILTERS = Object.freeze({
  query: "",
  packageTag: "",
  minPackages: "", maxPackages: "",
  minAnalyzed: "", maxAnalyzed: "",
  minTotalDecks: "", maxTotalDecks: "",
  minProfiles: "", maxProfiles: "",
  status: "all", coverage: "all", freshness: "all",
  sortBy: "unique_decks", direction: "desc",
});

export const BUILD_DISCOVERY_SORT_OPTIONS = [
  { value: "package_count", label: "Auto-selected packages" },
  { value: "unique_decks", label: "Analyzed decks" },
  { value: "total_edhrec_decks", label: "Total EDHREC decks" },
  { value: "profile_count", label: "Tag profiles" },
  { value: "coverage", label: "Index coverage" },
  { value: "generated_at", label: "Analysis date" },
  { value: "commander_name", label: "Commander name" },
];

const ENUM_VALUES = {
  status: ["all", "fitted", "insufficient_data"],
  coverage: ["all", "complete", "partial", "unknown"],
  freshness: ["all", "fresh", "needs_refresh", "unknown"],
  sortBy: BUILD_DISCOVERY_SORT_OPTIONS.map((option) => option.value),
  direction: ["asc", "desc"],
};

/** Keep invalid count text visible so shared links can explain validation errors. */
export function readBuildDiscoveryFilters(searchParams) {
  const params = searchParams instanceof URLSearchParams ? searchParams : new URLSearchParams(searchParams);
  return Object.fromEntries(Object.entries(DEFAULT_BUILD_DISCOVERY_FILTERS).map(([key, fallback]) => {
    const value = params.get(key);
    return [key, value === null || (ENUM_VALUES[key] && !ENUM_VALUES[key].includes(value)) ? fallback : value];
  }));
}

export function buildDiscoverySearchParams(filters = {}) {
  const params = new URLSearchParams();
  for (const [key, fallback] of Object.entries(DEFAULT_BUILD_DISCOVERY_FILTERS)) {
    const input = filters?.[key];
    const value = typeof input === "string" || typeof input === "number" ? String(input) : fallback;
    if (value !== fallback && (!ENUM_VALUES[key] || ENUM_VALUES[key].includes(value))) params.set(key, value);
  }
  return params;
}

const COUNT_RANGES = [
  { min: "minPackages", max: "maxPackages", field: "package_count", label: "Auto-selected packages" },
  { min: "minAnalyzed", max: "maxAnalyzed", field: "unique_decks", label: "Analyzed decks" },
  { min: "minTotalDecks", max: "maxTotalDecks", field: "total_edhrec_decks", label: "Total EDHREC decks" },
  { min: "minProfiles", max: "maxProfiles", field: "profile_count", label: "Tag profiles" },
];

function count(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function countInput(value) {
  if (value === undefined || value === null || (typeof value === "string" && !value.trim())) return null;
  if (typeof value === "number") return count(value) ?? NaN;
  return typeof value === "string" && /^\d+$/.test(value.trim())
    ? count(Number(value.trim())) ?? NaN : NaN;
}

export function getBuildDiscoveryFilterErrors(filters = {}) {
  const errors = [];
  for (const range of COUNT_RANGES) {
    const min = countInput(filters?.[range.min]);
    const max = countInput(filters?.[range.max]);
    if (Number.isNaN(min)) errors.push(`${range.label} minimum must be a nonnegative whole number.`);
    if (Number.isNaN(max)) errors.push(`${range.label} maximum must be a nonnegative whole number.`);
    if (min !== null && max !== null && Number.isFinite(min) && Number.isFinite(max) && min > max) {
      errors.push(`${range.label} minimum cannot exceed its maximum.`);
    }
  }
  return errors;
}

function searchable(value) {
  return String(value ?? "").normalize("NFKD").replace(/\p{M}/gu, "").replace(/['‘’ʼ]/g, "").toLowerCase();
}

function tagText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function tagKey(value) {
  return searchable(tagText(value)).replace(/\s+/g, " ");
}

function mainPackageTags(commander) {
  if (!Array.isArray(commander?.packages)) return [];
  return commander.packages.flatMap((pkg) => Array.isArray(pkg?.tag_suggestions) ? pkg.tag_suggestions : [])
    .filter((tag) => tag && typeof tag === "object" && !Array.isArray(tag)
      && tag.support_tier === "main"
      && (tagText(tag.tag_slug) || tagText(tag.tag_name)));
}

/** Match a complete supported package tag, keeping nearby themes such as Counters distinct. */
export function matchesBuildDiscoveryPackageTag(tag, selectedTag) {
  const selected = tagKey(selectedTag);
  if (!selected) return true;
  return Boolean(tag && typeof tag === "object" && !Array.isArray(tag)
    && [tag.tag_slug, tag.tag_name].some((value) => tagKey(value) === selected));
}

/** List main tags on primary-model packages and their distinct commander counts. */
export function getBuildDiscoveryPackageTags(commanders) {
  const occurrences = (Array.isArray(commanders) ? commanders : [])
    .filter((commander) => commander && typeof commander === "object" && !Array.isArray(commander))
    .flatMap((commander) => {
      const identity = tagKey(commander.commander_slug) ? `slug:${tagKey(commander.commander_slug)}`
        : tagKey(commander.commander_name) ? `name:${tagKey(commander.commander_name)}` : commander;
      return mainPackageTags(commander).map((tag) => ({
        commander: identity,
        slug: tagText(tag.tag_slug),
        name: tagText(tag.tag_name),
      }));
    });
  const options = new Map();
  const matchesByValue = new Map();
  // Slugged records supply the option value even when a name-only record was encountered first.
  for (const { commander, slug, name } of occurrences.toSorted((left, right) => Number(Boolean(right.slug)) - Number(Boolean(left.slug)))) {
    const identity = tagKey(slug || name);
    if (!options.has(identity)) options.set(identity, {
      value: slug || name, label: name || slug, hasName: Boolean(name),
    });
    const option = options.get(identity);
    if (name && !option.hasName) {
      option.label = name;
      option.hasName = true;
    }
    for (const key of new Set([slug, name].map(tagKey).filter(Boolean))) {
      if (!matchesByValue.has(key)) matchesByValue.set(key, new Set());
      matchesByValue.get(key).add(commander);
    }
  }
  // Match the option's actual value so counts stay consistent for incomplete or colliding labels.
  return [...options.values()].map(({ value, label }) => ({
    value, label, count: matchesByValue.get(tagKey(value)).size,
  }))
    .sort((left, right) => left.label.localeCompare(right.label, "en", { sensitivity: "base" })
      || left.value.localeCompare(right.value, "en"));
}

function commanderName(commander) {
  return String(commander.commander_name || commander.commander_slug || "");
}

function compareNames(left, right) {
  return commanderName(left).localeCompare(commanderName(right), "en", { sensitivity: "base" })
    || String(left.commander_slug || "").localeCompare(String(right.commander_slug || ""), "en");
}

function knownNumberOrder(left, right, direction) {
  if (left === null || right === null) return left === right ? 0 : left === null ? 1 : -1;
  return (left - right) * (direction === "asc" ? 1 : -1);
}

function coverageRatio(progress) {
  if (!progress.known || count(progress.frame) === null || count(progress.attempted) === null) return null;
  return progress.frame > 0 ? progress.attempted / progress.frame : progress.complete ? 1 : null;
}

function analysisDate(value) {
  const parsed = typeof value === "string" && value.trim() ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

/** Filter only the supplied reports; discovery eligibility remains the caller's responsibility. */
export function filterBuildDiscoveryCommanders(commanders, filters = {}, now = Date.now()) {
  const settings = { ...DEFAULT_BUILD_DISCOVERY_FILTERS, ...filters };
  if (getBuildDiscoveryFilterErrors(settings).length) return [];
  const words = searchable(settings.query).trim().split(/\s+/).filter(Boolean);
  const ranges = COUNT_RANGES.map((range) => ({
    ...range, minimum: countInput(settings[range.min]), maximum: countInput(settings[range.max]),
  })).filter((range) => range.minimum !== null || range.maximum !== null);
  const sortBy = BUILD_DISCOVERY_SORT_OPTIONS.some((option) => option.value === settings.sortBy)
    ? settings.sortBy : DEFAULT_BUILD_DISCOVERY_FILTERS.sortBy;
  const direction = settings.direction === "asc" ? "asc" : "desc";

  const reports = (Array.isArray(commanders) ? commanders : []).filter((commander) => commander && typeof commander === "object")
    .map((commander) => ({
      commander,
      progress: getBuildCollectionProgress(commander.collection, now),
      freshness: getBuildSourceFreshness(commander.collection, now).status,
    })).filter(({ commander, progress, freshness }) => {
      const search = searchable(`${commander.commander_name || ""} ${commander.commander_slug || ""}`);
      if (!words.every((word) => search.includes(word))) return false;
      if (tagKey(settings.packageTag) && !mainPackageTags(commander).some((tag) => matchesBuildDiscoveryPackageTag(tag, settings.packageTag))) return false;
      for (const range of ranges) {
        const value = count(commander[range.field]);
        if (value === null || (range.minimum !== null && value < range.minimum)
            || (range.maximum !== null && value > range.maximum)) return false;
      }
      if (["fitted", "insufficient_data"].includes(settings.status) && commander.status !== settings.status) return false;
      if (settings.coverage === "complete" && !progress.complete) return false;
      if (settings.coverage === "partial" && (!progress.known || progress.complete)) return false;
      if (settings.coverage === "unknown" && progress.known) return false;
      if (settings.freshness === "fresh" && freshness !== "fresh") return false;
      if (settings.freshness === "needs_refresh" && !["mixed", "stale"].includes(freshness)) return false;
      if (settings.freshness === "unknown" && freshness !== "unknown") return false;
      return true;
    });

  return reports.sort((left, right) => {
    if (sortBy === "commander_name") {
      return commanderName(left.commander).localeCompare(commanderName(right.commander), "en", { sensitivity: "base" })
        * (direction === "asc" ? 1 : -1) || compareNames(left.commander, right.commander);
    }
    const value = (report) => sortBy === "coverage" ? coverageRatio(report.progress)
      : sortBy === "generated_at" ? analysisDate(report.commander.generated_at) : count(report.commander[sortBy]);
    return knownNumberOrder(value(left), value(right), direction)
      || (sortBy === "package_count"
        ? knownNumberOrder(count(left.commander.unique_decks), count(right.commander.unique_decks), "desc") : 0)
      || compareNames(left.commander, right.commander);
  }).map(({ commander }) => commander);
}
