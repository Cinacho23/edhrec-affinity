import { asNumber } from "./tableUtils.js";

export const THEME_TREE_MIN_TOTAL_DECKS = 200;
export const THEME_TREE_MIN_TAG_DECKS = 5;

function uniqueSortedSlugs(slugs) {
  return [...new Set(slugs.filter((slug) => typeof slug === "string" && slug !== ""))].sort();
}

export function themeSubsetId(slugs) {
  return JSON.stringify(uniqueSortedSlugs(slugs));
}

export function getThemeTreeSubsetCount(tagCount) {
  if (!Number.isSafeInteger(tagCount) || tagCount < 0) {
    throw new RangeError("The number of themes must be a non-negative integer.");
  }
  return ((1n << BigInt(tagCount)) - 1n).toString();
}

export function getThemeTreeChildren(node) {
  return node.tagSlugs.length <= 1 ? [] : node.tagSlugs.map((_, index) =>
    node.tagSlugs.filter((_, childIndex) => childIndex !== index)
  );
}

export function combineThemeTreeSubsets(left, right) {
  return uniqueSortedSlugs([...left.tagSlugs, ...right.tagSlugs]);
}

/** Show every nonempty subset, connected to every immediate superset. */
export function buildThemeTreeNodes(selectedTags) {
  const tagsBySlug = new Map(selectedTags
    .filter((tag) => typeof tag?.tag_slug === "string" && tag.tag_slug !== "")
    .map((tag) => [tag.tag_slug, tag]));
  const slugs = [...tagsBySlug.keys()].sort();
  // Extend each existing subset once per theme. This produces the full power
  // set without a theme-count threshold or JavaScript's 32-bit bitwise limit.
  const subsets = [[]];
  for (const slug of slugs) {
    const previousCount = subsets.length;
    for (let index = 0; index < previousCount; index += 1) {
      subsets.push([...subsets[index], slug]);
    }
  }
  const nodes = subsets.slice(1).map((tagSlugs) => {
    const tags = tagSlugs.map((slug) => tagsBySlug.get(slug));
    return {
      id: JSON.stringify(tagSlugs),
      tagSlugs,
      tags,
      label: tags.map((tag) => tag.tag_name || tag.tag_slug).join(" + "),
    };
  }).sort((left, right) => left.tagSlugs.length - right.tagSlugs.length || left.id.localeCompare(right.id, "en"));
  const edges = [];
  // With all subsets present, an immediate connection removes exactly one
  // theme. Generate those edges directly instead of comparing every node pair.
  for (const node of nodes) {
    if (node.tagSlugs.length <= 1) continue;
    for (let index = 0; index < node.tagSlugs.length; index += 1) {
      const childSlugs = [...node.tagSlugs.slice(0, index), ...node.tagSlugs.slice(index + 1)];
      edges.push({ source: JSON.stringify(childSlugs), target: node.id });
    }
  }

  const totalPossibleNodes = getThemeTreeSubsetCount(slugs.length);
  return {
    nodes,
    edges,
    totalPossibleNodes,
    fullyExpanded: true,
    rootId: slugs.length ? themeSubsetId(slugs) : null,
  };
}

function compareCommanders(left, right) {
  const leftDecks = asNumber(left.total_decks);
  const rightDecks = asNumber(right.total_decks);
  if (leftDecks !== rightDecks) {
    if (leftDecks === null) return 1;
    if (rightDecks === null) return -1;
    return rightDecks - leftDecks;
  }
  return String(left.commander_name || left.commander_slug).localeCompare(
    String(right.commander_name || right.commander_slug), "en"
  ) || String(left.commander_slug).localeCompare(String(right.commander_slug), "en");
}

/** Filter every constituent tag before choosing each combination's winner. */
export function rankThemeTreeNodes(nodes, rowsByTag) {
  const qualifyingByTag = new Map();
  const selectedSlugs = new Set();
  for (const node of nodes) {
    for (const slug of node.tagSlugs) selectedSlugs.add(slug);
  }
  for (const slug of selectedSlugs) {
    const rows = rowsByTag.get(slug);
    if (!Array.isArray(rows)) throw new Error(`Missing complete tag data for ${slug}.`);
    const commanders = new Map();
    for (const row of rows) {
      const z = asNumber(row?.z);
      const totalDecks = asNumber(row?.total_decks);
      const tagDecks = asNumber(row?.tag_decks);
      if (
        !row?.commander_slug || row.tag_slug !== slug || z === null || z <= 1 ||
        totalDecks === null || totalDecks < THEME_TREE_MIN_TOTAL_DECKS ||
        tagDecks === null || tagDecks < THEME_TREE_MIN_TAG_DECKS
      ) continue;
      const previous = commanders.get(row.commander_slug);
      if (!previous || z > previous.z || (z === previous.z && compareCommanders(row, previous.row) < 0)) {
        commanders.set(row.commander_slug, { row, z });
      }
    }
    qualifyingByTag.set(slug, commanders);
  }

  const scoresBySubset = new Map();
  function getScores(tagSlugs) {
    const fullKey = JSON.stringify(tagSlugs);
    if (scoresBySubset.has(fullKey)) return scoresBySubset.get(fullKey);
    let scores;
    let firstMissingLength = 1;
    for (let length = tagSlugs.length - 1; length >= 1; length -= 1) {
      const cached = scoresBySubset.get(JSON.stringify(tagSlugs.slice(0, length)));
      if (cached) {
        scores = cached;
        firstMissingLength = length + 1;
        break;
      }
    }
    // Reuse each subset's cached prefix so only its final tag needs a new
    // intersection. Iteration also supports callers passing unordered nodes.
    for (let length = firstMissingLength; length <= tagSlugs.length; length += 1) {
      const key = JSON.stringify(tagSlugs.slice(0, length));
      const nextTag = qualifyingByTag.get(tagSlugs[length - 1]);
      const combined = new Map();
      if (length === 1) {
        for (const [slug, match] of nextTag) combined.set(slug, match.z);
      } else {
        const candidates = scores.size <= nextTag.size ? scores.keys() : nextTag.keys();
        for (const slug of candidates) {
          const previous = scores.get(slug);
          const match = nextTag.get(slug);
          if (previous === undefined || !match) continue;
          const sum = previous + match.z;
          if (Number.isFinite(sum)) combined.set(slug, sum);
        }
      }
      scoresBySubset.set(key, combined);
      scores = combined;
    }
    return scores || new Map();
  }

  return nodes.map((node) => {
    const scores = getScores(node.tagSlugs);
    const firstTag = qualifyingByTag.get(node.tagSlugs[0]);
    let winningRow = null;
    let winningScore = null;
    for (const [slug, score] of scores) {
      const candidate = firstTag.get(slug).row;
      if (!winningRow || score > winningScore || (
        score === winningScore && compareCommanders(candidate, winningRow) < 0
      )) {
        winningRow = candidate;
        winningScore = score;
      }
    }
    const winner = winningRow && {
      ...winningRow,
      cumulative_z: winningScore,
      cumulative_tag_scores: node.tagSlugs.map((tag_slug, index) => ({
        tag_slug,
        tag_name: node.tags[index].tag_name || tag_slug,
        z: qualifyingByTag.get(tag_slug).get(winningRow.commander_slug).z,
      })),
    };
    return { ...node, winner, qualifyingCommanderCount: scores.size };
  });
}
