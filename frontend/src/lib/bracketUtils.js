export const CEDH_TAG_SLUG = "cedh";

export const ARCHETYPE_TAG_SLUGS = new Set([
  "aggro",
  "control",
  "midrange",
  "tempo",
  "combo",
]);

export const THEME_BRACKET_MIN_Z = 1.05;

export function themeUsesBracketRulesOnly(themeSlug) {
  const normalizedThemeSlug = normalizeTagSlug({ tag_slug: themeSlug });

  return (
    normalizedThemeSlug === CEDH_TAG_SLUG ||
    ARCHETYPE_TAG_SLUGS.has(normalizedThemeSlug)
  );
}

export const BRACKET_OPTIONS = [
  { key: "5", label: "Bracket 5", rank: 5 },
  { key: "4/5", label: "Bracket 4/5", rank: 4.5 },
  { key: "4", label: "Bracket 4", rank: 4 },
  { key: "3", label: "Bracket 3", rank: 3 },
  { key: "2/3", label: "Bracket 2/3", rank: 2.5 },
  { key: "2", label: "Bracket 2", rank: 2 },
  { key: "1/2", label: "Bracket 1/2", rank: 1.5 },
  { key: "1", label: "Bracket 1", rank: 1 },
];

const BRACKET_BY_KEY = new Map(
  BRACKET_OPTIONS.map((bracket) => [bracket.key, bracket])
);

function normalizeTagSlug(row) {
  return String(row?.tag_slug || row?.tag_name || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function getFiniteZ(row) {
  if (row?.z === null || row?.z === undefined || row?.z === "") {
    return null;
  }

  const score = Number(row.z);
  return Number.isFinite(score) ? score : null;
}

function getFiniteNumber(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function getHighestScoredRow(rows, acceptedTags) {
  let highest = null;

  for (const row of rows || []) {
    if (!acceptedTags.has(normalizeTagSlug(row))) {
      continue;
    }

    const score = getFiniteZ(row);

    if (score === null || (highest && score <= highest.score)) {
      continue;
    }

    highest = { row, score };
  }

  return highest;
}

function createClassification(key, scoredRow, reason) {
  const bracket = BRACKET_BY_KEY.get(key);

  return {
    bracket_key: bracket.key,
    bracket_label: bracket.label,
    bracket_rank: bracket.rank,
    decision_tag_name: scoredRow?.row?.tag_name || null,
    decision_tag_slug: scoredRow ? normalizeTagSlug(scoredRow.row) : null,
    decision_z: scoredRow?.score ?? null,
    decision_tag_decks: scoredRow?.row?.tag_decks ?? null,
    bracket_reason: reason,
  };
}

/**
 * Classify one commander's tag rows.
 *
 * cEDH is evaluated before the five archetype tags, so a qualifying cEDH
 * score always wins even when an archetype score would otherwise be Bracket 3.
 * The open intervals from 0.95 to 1.05 are the upper in-flux bands. A small
 * positive archetype score below 0.05 is the Bracket 1/2 in-flux band.
 */
export function classifyCommanderRows(rows) {
  const cedh = getHighestScoredRow(rows, new Set([CEDH_TAG_SLUG]));

  if (cedh?.score >= 1.05) {
    return createClassification(
      "5",
      cedh,
      "cEDH z-score is at least 1.05."
    );
  }

  if (cedh?.score > 0.95) {
    return createClassification(
      "4/5",
      cedh,
      "cEDH z-score is between 0.95 and 1.05."
    );
  }

  if (cedh?.score >= 0.05) {
    return createClassification(
      "4",
      cedh,
      "cEDH z-score is from 0.05 through 0.95."
    );
  }

  const archetype = getHighestScoredRow(rows, ARCHETYPE_TAG_SLUGS);

  if (archetype?.score >= 1.05) {
    return createClassification(
      "3",
      archetype,
      `${archetype.row.tag_name} z-score is at least 1.05.`
    );
  }

  if (archetype?.score > 0.95) {
    return createClassification(
      "2/3",
      archetype,
      `${archetype.row.tag_name} z-score is between 0.95 and 1.05.`
    );
  }

  if (archetype?.score >= 0.05) {
    return createClassification(
      "2",
      archetype,
      `${archetype.row.tag_name} z-score is from 0.05 through 0.95.`
    );
  }

  if (archetype?.score > 0) {
    return createClassification(
      "1/2",
      archetype,
      `${archetype.row.tag_name} z-score is positive but below 0.05.`
    );
  }

  return createClassification(
    "1",
    archetype,
    "No cEDH or archetype tag reaches a higher bracket threshold."
  );
}

export function buildCommanderBracketRows(rows) {
  const groups = new Map();

  for (const row of rows || []) {
    const groupKey =
      row?.commander_slug || row?.commander_name || `unknown-${groups.size}`;

    if (!groups.has(groupKey)) {
      groups.set(groupKey, []);
    }

    groups.get(groupKey).push(row);
  }

  return [...groups.entries()].map(([groupKey, commanderRows]) => {
    const commander = commanderRows[0] || {};

    return {
      ...commander,
      id: groupKey,
      ...classifyCommanderRows(commanderRows),
    };
  });
}

function getThemeCandidate(rows, themeSlug) {
  let highest = null;

  function consider(candidate, container, usesThemeFields = false) {
    const candidateSlug = normalizeTagSlug(
      usesThemeFields
        ? {
            tag_slug: container?.theme_tag_slug,
            tag_name: container?.theme_tag_name,
          }
        : candidate
    );

    if (candidateSlug !== themeSlug) {
      return;
    }

    const score = getFiniteNumber(
      usesThemeFields ? container?.theme_z : candidate?.z
    );

    if (
      highest &&
      (score === null || (highest.score !== null && score <= highest.score))
    ) {
      return;
    }

    highest = {
      score,
      name: usesThemeFields
        ? container?.theme_tag_name
        : candidate?.tag_name,
      decks: usesThemeFields
        ? container?.theme_tag_decks
        : candidate?.tag_decks,
      affinity: usesThemeFields
        ? container?.theme_affinity_pct
        : candidate?.tag_affinity_pct,
    };
  }

  for (const row of rows) {
    consider(row, row);

    if (row?.theme_tag_slug || row?.theme_tag_name) {
      consider(row, row, true);
    }

    for (const signalRow of row?.bracket_tag_rows || []) {
      consider(signalRow, row);
    }
  }

  return highest;
}

/**
 * Build one bracket result per commander for a selected theme.
 *
 * A commander is included only when its selected-theme z-score reaches the
 * theme threshold. Bracket assignment remains entirely delegated to
 * classifyCommanderRows, preserving the exact cEDH precedence and archetype
 * boundary behavior used by the set-based Brackets page.
 *
 * Rows can either be normal flat tag rows or compact theme export rows with a
 * bracket_tag_rows array. Supporting both shapes keeps older static datasets
 * usable through the frontend's commander-detail fallback.
 */
export function buildCommanderThemeBracketRows(
  rows,
  themeSlug,
  minThemeZ = THEME_BRACKET_MIN_Z
) {
  const normalizedThemeSlug = normalizeTagSlug({ tag_slug: themeSlug });
  const usesBracketRulesOnly = themeUsesBracketRulesOnly(
    normalizedThemeSlug
  );

  if (!normalizedThemeSlug) {
    return [];
  }

  const groups = new Map();

  for (const row of rows || []) {
    const groupKey =
      row?.commander_slug || row?.commander_name || `unknown-${groups.size}`;

    if (!groups.has(groupKey)) {
      groups.set(groupKey, []);
    }

    groups.get(groupKey).push(row);
  }

  const results = [];

  for (const [groupKey, commanderRows] of groups) {
    const theme = getThemeCandidate(commanderRows, normalizedThemeSlug);

    if (
      !theme ||
      (!usesBracketRulesOnly &&
        (theme.score === null || theme.score < minThemeZ))
    ) {
      continue;
    }

    const classificationRows = commanderRows.flatMap((row) =>
      Array.isArray(row?.bracket_tag_rows) ? row.bracket_tag_rows : [row]
    );
    const commander = { ...(commanderRows[0] || {}) };
    delete commander.bracket_tag_rows;

    results.push({
      ...commander,
      id: groupKey,
      theme_tag_name: theme.name || normalizedThemeSlug,
      theme_tag_slug: normalizedThemeSlug,
      theme_z: theme.score,
      theme_tag_decks: theme.decks ?? null,
      theme_affinity_pct: theme.affinity ?? null,
      ...classifyCommanderRows(classificationRows),
    });
  }

  return results;
}
