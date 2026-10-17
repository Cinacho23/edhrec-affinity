import { formatDecimal } from "./formatters.js";
import { asNumber } from "./tableUtils.js";

function normalizeTag(value) {
  return String(value ?? "").trim().toLowerCase().replace(/[-_\s]+/g, " ");
}

/** Match complete names/slugs so multi-word tags remain one filter. */
export function resolveCumulativeTags(query, tags) {
  const byName = new Map();
  for (const tag of tags) {
    byName.set(normalizeTag(tag.tag_name), tag);
    byName.set(normalizeTag(tag.tag_slug), tag);
  }
  const selected = new Map();
  const unmatched = [];
  for (const part of String(query).split(/[,;\n]+/).map((value) => value.trim()).filter(Boolean)) {
    const parts = byName.has(normalizeTag(part)) ? [part] : part.split(/\s+and\s+/i);
    for (const name of parts) {
      const tag = byName.get(normalizeTag(name));
      if (tag) selected.set(tag.tag_slug, tag);
      else unmatched.push(name.trim());
    }
  }
  return { selectedTags: [...selected.values()], unmatched };
}

/** Intersect complete tag files; never sum duplicate tags or use legacy scores. */
export function buildCumulativeTagScores(tagGroups) {
  const groups = new Map(tagGroups.map((group) => [group.tag_slug, group]));
  let scores = null;
  for (const { tag_slug, tag_name, rows } of groups.values()) {
    const qualifying = new Map();
    for (const row of rows) {
      const z = asNumber(row.z);
      if (!row.commander_slug || row.tag_slug !== tag_slug || z === null || z <= 1) continue;
      const previous = qualifying.get(row.commander_slug);
      if (!previous || z > previous.z) qualifying.set(row.commander_slug, { z, row });
    }
    if (scores === null) {
      scores = new Map([...qualifying].map(([slug, { z, row }]) => [slug, {
        commander: row,
        cumulative_z: z,
        cumulative_tag_scores: [{ tag_slug, tag_name, z }],
      }]));
    } else {
      for (const [slug, result] of scores) {
        const match = qualifying.get(slug);
        if (!match) scores.delete(slug);
        else {
          result.cumulative_z += match.z;
          result.cumulative_tag_scores.push({ tag_slug, tag_name, z: match.z });
        }
      }
    }
  }
  return scores ?? new Map();
}

export function applyCumulativeTagScores(rows, scores) {
  return rows.filter((row) => scores.has(row.commander_slug)).map((row) => {
    const { cumulative_z, cumulative_tag_scores } = scores.get(row.commander_slug);
    return { ...row, cumulative_z, cumulative_tag_scores };
  });
}

export const cumulativeZColumn = {
  key: "cumulative_z",
  header: "Cumulative Z",
  sortable: true,
  render: (row) => formatDecimal(row.cumulative_z),
};
