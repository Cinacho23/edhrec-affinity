import { asNumber } from "./tableUtils.js";

const AUTOMATIC_SUBSET_LIMIT = 7;
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

/**
 * Show every nonempty subset for up to seven themes. Larger selections start
 * with single themes, a prefix chain, and the root, then keep every explicitly
 * requested subset. Edges are the transitive reduction of the visible subsets.
 */
export function buildThemeTreeNodes(selectedTags, expandedSubsets = []) {
  const tagsBySlug = new Map(selectedTags
    .filter((tag) => typeof tag?.tag_slug === "string" && tag.tag_slug !== "")
    .map((tag) => [tag.tag_slug, tag]));
  const slugs = [...tagsBySlug.keys()].sort();
  const subsets = new Map();
  const addSubset = (tagSlugs) => {
    const canonical = uniqueSortedSlugs(tagSlugs);
    if (canonical.length === 0 || canonical.some((slug) => !tagsBySlug.has(slug))) return;
    subsets.set(themeSubsetId(canonical), canonical);
  };

  if (slugs.length <= AUTOMATIC_SUBSET_LIMIT) {
    for (let mask = 1; mask < 2 ** slugs.length; mask += 1) {
      addSubset(slugs.filter((_, index) => mask & (1 << index)));
    }
  } else {
    for (const slug of slugs) addSubset([slug]);
    for (let length = 2; length <= slugs.length; length += 1) addSubset(slugs.slice(0, length));
  }
  for (const subset of expandedSubsets) addSubset(subset);

  const nodes = [...subsets.entries()].map(([id, tagSlugs]) => {
    const tags = tagSlugs.map((slug) => tagsBySlug.get(slug));
    return { id, tagSlugs, tags, label: tags.map((tag) => tag.tag_name || tag.tag_slug).join(" + ") };
  }).sort((left, right) => left.tagSlugs.length - right.tagSlugs.length || left.id.localeCompare(right.id, "en"));
  const bits = new Map(slugs.map((slug, index) => [slug, 1n << BigInt(index)]));
  const masks = new Map(nodes.map((node) => [node.id,
    node.tagSlugs.reduce((mask, slug) => mask | bits.get(slug), 0n),
  ]));
  const edges = [];

  for (const node of nodes) {
    const mask = masks.get(node.id);
    const nearestSupersets = [];
    for (const candidate of nodes) {
      if (candidate.tagSlugs.length <= node.tagSlugs.length) continue;
      const candidateMask = masks.get(candidate.id);
      if ((mask & candidateMask) !== mask) continue;
      if (nearestSupersets.some((nearer) => (nearer & candidateMask) === nearer)) continue;
      nearestSupersets.push(candidateMask);
      edges.push({ source: node.id, target: candidate.id });
    }
  }

  const totalPossibleNodes = getThemeTreeSubsetCount(slugs.length);
  return {
    nodes,
    edges,
    totalPossibleNodes,
    fullyExpanded: BigInt(nodes.length) === BigInt(totalPossibleNodes),
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
  const selectedSlugs = new Set(nodes.flatMap((node) => node.tagSlugs));
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
    // Prefix reuse makes a large initial graph linear in theme-file size,
    // rather than rescanning every tag of every prefix for each commander.
    // Iteration also avoids a recursion limit for large selections.
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
