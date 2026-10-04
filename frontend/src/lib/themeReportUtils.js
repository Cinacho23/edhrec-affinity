import { buildCommanderThemeBracketRows } from "./bracketUtils.js";
import { passesMin, rowMatchesText } from "./tableUtils.js";

export const DEFAULT_THEME_REPORT_FILTERS = {
  themeQuery: "",
  commanderQuery: "",
  brackets: [],
  minTotalDecks: "200",
  minThemeDecks: "5",
  groupBy: "theme",
  topCount: "1",
};

/** Classify the downloaded data once, independently of report filters. */
export function prepareThemeReportRows(themeGroups) {
  return (themeGroups || []).flatMap((theme) =>
    buildCommanderThemeBracketRows(theme.rows, theme.tag_slug).map((row) => ({
      ...row,
      id: JSON.stringify([row.theme_tag_slug, row.id]),
      theme_tag_name: theme.tag_name || row.theme_tag_name,
    }))
  );
}

function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function compareText(a, b) {
  return String(a || "").localeCompare(String(b || ""), "en");
}

function compareDescendingNumbers(a, b) {
  const aNumber = finiteNumber(a);
  const bNumber = finiteNumber(b);
  if (aNumber === null) return bNumber === null ? 0 : 1;
  if (bNumber === null) return -1;
  return bNumber - aNumber;
}

/**
 * Rank by theme z-score, breaking ties by theme decks, total decks (both
 * descending), then commander name and slug (alphabetically). Every filter is
 * applied before selecting the top N, so a filtered-out winner cannot hide the
 * next eligible commander. Each theme/bracket pair has its own rank in bracket
 * mode. Missing or non-finite scores cannot win a z-score report, including for
 * the six themes that do not have an ordinary-theme z-score eligibility gate.
 */
export function buildThemeReportRows(preparedRows, filters = {}) {
  const activeFilters = { ...DEFAULT_THEME_REPORT_FILTERS, ...filters };
  const requestedCount = Number(activeFilters.topCount);
  const topCount = Number.isFinite(requestedCount)
    ? Math.max(1, Math.floor(requestedCount))
    : 1;
  const selectedBrackets = new Set(
    Array.isArray(activeFilters.brackets) ? activeFilters.brackets : []
  );
  const perBracket = activeFilters.groupBy === "bracket";
  const groups = new Map();

  for (const row of preparedRows || []) {
    if (
      finiteNumber(row.theme_z) === null ||
      !rowMatchesText(row, activeFilters.themeQuery, [
        "theme_tag_name",
        "theme_tag_slug",
      ]) ||
      !rowMatchesText(row, activeFilters.commanderQuery, [
        "commander_name",
        "commander_slug",
        "decision_tag_name",
      ]) ||
      !passesMin(row, "total_decks", activeFilters.minTotalDecks) ||
      !passesMin(row, "theme_tag_decks", activeFilters.minThemeDecks) ||
      (selectedBrackets.size > 0 && !selectedBrackets.has(row.bracket_key))
    ) {
      continue;
    }

    const groupKey = JSON.stringify([
      row.theme_tag_slug,
      perBracket ? row.bracket_key : null,
    ]);
    if (!groups.has(groupKey)) groups.set(groupKey, []);
    groups.get(groupKey).push(row);
  }

  const reportRows = [...groups.values()].flatMap((rows) =>
    rows
      .sort(
        (a, b) =>
          compareDescendingNumbers(a.theme_z, b.theme_z) ||
          compareDescendingNumbers(a.theme_tag_decks, b.theme_tag_decks) ||
          compareDescendingNumbers(a.total_decks, b.total_decks) ||
          compareText(a.commander_name, b.commander_name) ||
          compareText(a.commander_slug, b.commander_slug) ||
          compareText(a.id, b.id)
      )
      .slice(0, topCount)
      .map((row, index) => ({ ...row, report_rank: index + 1 }))
  );

  return reportRows.sort(
    (a, b) =>
      compareText(a.theme_tag_name, b.theme_tag_name) ||
      compareText(a.theme_tag_slug, b.theme_tag_slug) ||
      (perBracket ? b.bracket_rank - a.bracket_rank : 0) ||
      a.report_rank - b.report_rank
  );
}
