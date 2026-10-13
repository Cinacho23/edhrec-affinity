import { getBuildRarity, getThemeAffinityMetrics } from "./affinityDisplay.js";
import { classifyCommanderRows } from "./bracketUtils.js";
import { asNumber, compareValues, passesMin, rowMatchesText } from "./tableUtils.js";

export const DEFAULT_QUIRKY_BUILDS_FILTERS = {
  themeQuery: "",
  commanderQuery: "",
  brackets: [],
  minTotalDecks: "200",
  minThemeDecks: "5",
  minBuildRarity: "50",
  groupBy: "theme",
  topCount: "1",
};

// Unlike Theme Report, preparation has no theme z-score eligibility gate.
// The shared signals determine commander brackets independently of the build.
export function prepareQuirkyBuildsRows(themeGroups) {
  const brackets = new Map();
  const rows = [];

  for (const theme of themeGroups || []) {
    for (const source of theme.rows || []) {
      if (!brackets.has(source.commander_slug)) {
        brackets.set(source.commander_slug, classifyCommanderRows(source.bracket_tag_rows));
      }

      const row = { ...source };
      delete row.bracket_tag_rows;
      rows.push({
        ...row,
        ...brackets.get(source.commander_slug),
        id: JSON.stringify([theme.tag_slug, source.commander_slug]),
        theme_tag_slug: theme.tag_slug,
        theme_tag_name: theme.tag_name || theme.tag_slug,
      });
    }
  }

  return rows;
}

function compareText(a, b) {
  return String(a || "").localeCompare(String(b || ""), "en");
}

/** Filter before selecting the rarest N builds in each theme/bracket group. */
export function buildQuirkyBuildsRows(preparedRows, filters = {}) {
  const active = { ...DEFAULT_QUIRKY_BUILDS_FILTERS, ...filters };
  const requestedCount = Number(active.topCount);
  const topCount = Number.isFinite(requestedCount)
    ? Math.max(1, Math.floor(requestedCount))
    : 1;
  const selectedBrackets = new Set(Array.isArray(active.brackets) ? active.brackets : []);
  const perBracket = active.groupBy === "bracket";
  const groups = new Map();

  for (const row of preparedRows || []) {
    const rarity = getBuildRarity(getThemeAffinityMetrics(row));
    const taggedDecks = asNumber(row.theme_tag_decks);
    const totalDecks = asNumber(row.total_decks);

    if (
      rarity === null || taggedDecks === null || taggedDecks <= 0 ||
      totalDecks === null || totalDecks < taggedDecks ||
      !rowMatchesText(row, active.themeQuery, ["theme_tag_name", "theme_tag_slug"]) ||
      !rowMatchesText(row, active.commanderQuery, ["commander_name", "commander_slug", "decision_tag_name"]) ||
      !passesMin(row, "total_decks", active.minTotalDecks) ||
      !passesMin(row, "theme_tag_decks", active.minThemeDecks) ||
      !passesMin(row, "theme_build_rarity", active.minBuildRarity) ||
      (selectedBrackets.size > 0 && !selectedBrackets.has(row.bracket_key))
    ) {
      continue;
    }

    const key = JSON.stringify([row.theme_tag_slug, perBracket ? row.bracket_key : null]);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push({ ...row, theme_build_rarity: rarity });
  }

  const ranked = [...groups.values()].flatMap((rows) =>
    [...rows].sort((a, b) =>
      compareValues(a.theme_build_rarity, b.theme_build_rarity, "desc") ||
      compareValues(a.theme_tag_decks, b.theme_tag_decks, "desc") ||
      compareValues(a.total_decks, b.total_decks, "desc") ||
      compareText(a.commander_name, b.commander_name) ||
      compareText(a.commander_slug, b.commander_slug) ||
      compareText(a.id, b.id)
    ).slice(0, topCount).map((row, index) => ({ ...row, report_rank: index + 1 }))
  );

  return ranked.sort((a, b) =>
    compareText(a.theme_tag_name, b.theme_tag_name) ||
    compareText(a.theme_tag_slug, b.theme_tag_slug) ||
    (perBracket ? b.bracket_rank - a.bracket_rank : 0) ||
    a.report_rank - b.report_rank
  );
}
