export const CEDH_TAG_SLUG = "cedh";

export const ARCHETYPE_TAG_SLUGS = new Set([
  "aggro",
  "control",
  "midrange",
  "tempo",
  "combo",
]);

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

