/*
  filterUtils.js

  Shared helper functions for:
  - text normalization
  - safe number parsing
  - row filtering
  - chart data preparation
  - display formatting

  Keeping these helpers outside the page components makes the React pages
  easier to read and easier to debug.
*/

export function normalizeText(value) {
  return String(value ?? "")
    .trim()
    .toLowerCase();
}

export function getNumber(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  const numericValue = Number(value);

  if (Number.isNaN(numericValue)) {
    return null;
  }

  return numericValue;
}

export function formatNumber(value) {
  const numericValue = getNumber(value);

  if (numericValue === null) {
    return "—";
  }

  return numericValue.toLocaleString();
}

export function formatDecimal(value, digits = 2) {
  const numericValue = getNumber(value);

  if (numericValue === null) {
    return "—";
  }

  return numericValue.toFixed(digits);
}

export function formatZScore(value) {
  const numericValue = getNumber(value);

  if (numericValue === null) {
    return "—";
  }

  return numericValue.toFixed(2);
}

export function formatPercentFromDecimal(value) {
  const numericValue = getNumber(value);

  if (numericValue === null) {
    return "—";
  }

  return `${(numericValue * 100).toFixed(2)}%`;
}

export function formatPercentFromDisplayValue(value) {
  const numericValue = getNumber(value);

  if (numericValue === null) {
    return "—";
  }

  return `${numericValue.toFixed(2)}%`;
}

export function getAffinityDisplayPct(row) {
  /*
    Chat 7 added tag_affinity_pct_display for frontend formatting.
    If it is missing, fall back to tag_affinity_pct as a decimal.
  */
  const displayValue = getNumber(row.tag_affinity_pct_display);

  if (displayValue !== null) {
    return displayValue;
  }

  const decimalValue = getNumber(row.tag_affinity_pct);

  if (decimalValue !== null) {
    return decimalValue * 100;
  }

  const legacyPct = getNumber(row.pct);

  if (legacyPct !== null) {
    return legacyPct;
  }

  return null;
}

export function getColorIdentity(row) {
  /*
    Different pipeline stages may use slightly different names.
    This helper gives the UI one stable way to read color identity.
  */
  return (
    row.color_identity ??
    row.colour_identity ??
    row.Colour ??
    row.colour ??
    row.color ??
    "—"
  );
}

export function getTrendStatus(row) {
  return (
    row.snapshot_status ??
    row.trend_status ??
    row.status_type ??
    row.status ??
    ""
  );
}

export function textIncludes(haystack, needle) {
  const normalizedNeedle = normalizeText(needle);

  if (!normalizedNeedle) {
    return true;
  }

  return normalizeText(haystack).includes(normalizedNeedle);
}

export function passesMinimum(value, minimum) {
  const numericValue = getNumber(value);
  const numericMinimum = getNumber(minimum);

  if (numericMinimum === null) {
    return true;
  }

  if (numericValue === null) {
    return false;
  }

  return numericValue >= numericMinimum;
}

export function rowMatchesLeaderboardFilters(row, filters) {
  const commanderName = row.commander_name ?? row.commander ?? "";
  const tagName = row.tag_name ?? row.tag ?? "";
  const colorIdentity = getColorIdentity(row);
  const affinityDisplayPct = getAffinityDisplayPct(row);
  const trendStatus = getTrendStatus(row);

  if (!textIncludes(commanderName, filters.commanderText)) {
    return false;
  }

  if (!textIncludes(tagName, filters.tagText)) {
    return false;
  }

  if (!textIncludes(colorIdentity, filters.colorIdentity)) {
    return false;
  }

  if (!passesMinimum(row.total_decks, filters.minTotalDecks)) {
    return false;
  }

  if (!passesMinimum(row.tag_decks, filters.minTagDecks)) {
    return false;
  }

  if (!passesMinimum(row.z, filters.minZ)) {
    return false;
  }

  if (!passesMinimum(affinityDisplayPct, filters.minAffinityPct)) {
    return false;
  }

  if (filters.trendStatus && trendStatus !== filters.trendStatus) {
    return false;
  }

  return true;
}

export function rowMatchesTagExplorerFilters(row, filters) {
  const commanderName = row.commander_name ?? row.commander ?? "";
  const colorIdentity = getColorIdentity(row);
  const affinityDisplayPct = getAffinityDisplayPct(row);
  const trendStatus = getTrendStatus(row);

  if (!textIncludes(commanderName, filters.commanderText)) {
    return false;
  }

  if (!textIncludes(colorIdentity, filters.colorIdentity)) {
    return false;
  }

  if (!passesMinimum(row.total_decks, filters.minTotalDecks)) {
    return false;
  }

  if (!passesMinimum(row.tag_decks, filters.minTagDecks)) {
    return false;
  }

  if (!passesMinimum(row.z, filters.minZ)) {
    return false;
  }

  if (!passesMinimum(affinityDisplayPct, filters.minAffinityPct)) {
    return false;
  }

  if (filters.trendStatus && trendStatus !== filters.trendStatus) {
    return false;
  }

  return true;
}

export function buildTagOptionsFromRows(rows) {
  const tagMap = new Map();

  for (const row of rows) {
    const slug = row.tag_slug ?? row.tagSlug ?? row.tag_name ?? row.tag;

    if (!slug) {
      continue;
    }

    const name = row.tag_name ?? row.tag ?? slug;

    if (!tagMap.has(slug)) {
      tagMap.set(slug, {
        slug,
        name,
      });
    }
  }

  return Array.from(tagMap.values()).sort((a, b) =>
    a.name.localeCompare(b.name)
  );
}

export function buildTagOptionsFromSummary(tagSummaryRows) {
  if (!Array.isArray(tagSummaryRows)) {
    return [];
  }

  return tagSummaryRows
    .map((row) => ({
      slug: row.tag_slug ?? row.slug ?? row.tag_name,
      name: row.tag_name ?? row.name ?? row.tag_slug ?? "Unknown tag",
    }))
    .filter((tag) => tag.slug)
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function findTagSummary(tagSummaryRows, selectedTagSlug) {
  if (!selectedTagSlug || !Array.isArray(tagSummaryRows)) {
    return null;
  }

  return (
    tagSummaryRows.find(
      (row) =>
        row.tag_slug === selectedTagSlug ||
        row.slug === selectedTagSlug ||
        row.tag_name === selectedTagSlug
    ) ?? null
  );
}

export function getSelectedTagName(tagOptions, selectedTagSlug) {
  return (
    tagOptions.find((tag) => tag.slug === selectedTagSlug)?.name ??
    selectedTagSlug ??
    ""
  );
}

export function makeTopZChartData(rows, limit = 10) {
  return rows
    .filter((row) => getNumber(row.z) !== null)
    .slice()
    .sort((a, b) => getNumber(b.z) - getNumber(a.z))
    .slice(0, limit)
    .map((row) => ({
      name: row.commander_name ?? row.commander ?? "Unknown",
      value: Number(getNumber(row.z).toFixed(2)),
    }));
}

export function makeTopTagDeckChartData(rows, limit = 10) {
  return rows
    .filter((row) => getNumber(row.tag_decks) !== null)
    .slice()
    .sort((a, b) => getNumber(b.tag_decks) - getNumber(a.tag_decks))
    .slice(0, limit)
    .map((row) => ({
      name: row.commander_name ?? row.commander ?? "Unknown",
      value: getNumber(row.tag_decks),
    }));
}