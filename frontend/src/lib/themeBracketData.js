import * as defaultDataSource from "./api.js";
import {
  ARCHETYPE_TAG_SLUGS,
  CEDH_TAG_SLUG,
  THEME_BRACKET_MIN_Z,
  classifyCommanderRows,
  getBracketScore,
  themeUsesBracketRulesOnly,
} from "./bracketUtils.js";

const DEFAULT_CONCURRENCY = 8;
const BRACKET_SIGNAL_TAG_SLUGS = [CEDH_TAG_SLUG, ...ARCHETYPE_TAG_SLUGS];

function validateConcurrency(concurrency) {
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new RangeError("Concurrency must be a positive integer.");
  }
}

export async function mapWithConcurrency(items, concurrency, mapper) {
  validateConcurrency(concurrency);
  const results = new Array(items.length);
  let nextIndex = 0;
  let failed = false;

  async function worker() {
    while (!failed && nextIndex < items.length) {
      const currentIndex = nextIndex;
      nextIndex += 1;

      try {
        results[currentIndex] = await mapper(items[currentIndex], currentIndex);
      } catch (error) {
        failed = true;
        throw error;
      }
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, () => worker())
  );
  return results;
}

function createRequestLimiter(concurrency) {
  validateConcurrency(concurrency);
  const queue = [];
  let active = 0;

  function startNext() {
    while (active < concurrency && queue.length > 0) {
      const { request, resolve, reject } = queue.shift();
      active += 1;
      Promise.resolve()
        .then(request)
        .then(resolve, reject)
        .finally(() => {
          active -= 1;
          startNext();
        });
    }
  }

  return (request) =>
    new Promise((resolve, reject) => {
      queue.push({ request, resolve, reject });
      startNext();
    });
}

function qualifiesForTheme(row, themeSlug) {
  if (themeUsesBracketRulesOnly(themeSlug)) return true;
  const score = getBracketScore(row);
  return score !== null && score >= THEME_BRACKET_MIN_Z;
}

function createThemeRow(themeRow, bracketTagRows) {
  return {
    ...themeRow,
    theme_tag_name: themeRow.tag_name,
    theme_tag_slug: themeRow.tag_slug,
    theme_z: getBracketScore(themeRow),
    theme_affinity_z: themeRow.z ?? null,
    ...(Object.hasOwn(themeRow, "legacy_z") ? { theme_legacy_z: themeRow.legacy_z } : {}),
    theme_tag_decks: themeRow.tag_decks,
    theme_affinity_pct: themeRow.tag_affinity_pct,
    theme_affinity_adjusted_pct: themeRow.tag_affinity_adjusted_pct ?? null,
    theme_affinity_lower_pct: themeRow.tag_affinity_lower_pct ?? null,
    theme_affinity_upper_pct: themeRow.tag_affinity_upper_pct ?? null,
    theme_affinity_model_status: themeRow.affinity_model_status ?? null,
    theme_affinity_model_version: themeRow.affinity_model_version ?? null,
    theme_rank_within_tag_by_z: themeRow.rank_within_tag_by_z ?? null,
    bracket_tag_rows: bracketTagRows,
  };
}

function hasDecidingSignalTie(rows) {
  const decision = classifyCommanderRows(rows);
  if (decision.decision_z === null) return false;

  const tags = decision.decision_tag_slug === CEDH_TAG_SLUG
    ? new Set([CEDH_TAG_SLUG])
    : ARCHETYPE_TAG_SLUGS;

  return rows.filter(
    (row) =>
      tags.has(row.tag_slug) &&
      getBracketScore(row) === decision.decision_z
  ).length > 1;
}

/**
 * Share requests across themes and keep a global cap on in-flight data files.
 * Older exports have complete tag files, so six of them contain every signal
 * required for bracket classification without loading thousands of commanders.
 * A dataSource override supports testing the same modern and legacy paths.
 */
export function createThemeBracketDataLoader({
  concurrency = DEFAULT_CONCURRENCY,
  dataSource,
} = {}) {
  const limitRequest = createRequestLimiter(concurrency);
  const requests = new Map();
  const themeRows = new Map();
  const commanderSignals = new Map();
  let indexPromise;
  let signalRowsPromise;

  function requestRows(method, slug = "") {
    const key = `${method}:${slug}`;
    if (!requests.has(key)) {
      requests.set(key, limitRequest(async () => {
        const source = dataSource || defaultDataSource;
        const rows = await source[method](slug);

        if (!Array.isArray(rows)) {
          throw new Error(`Expected a list of rows from ${method}${slug ? ` (${slug})` : ""}.`);
        }

        return rows;
      }));
    }
    return requests.get(key);
  }

  function loadIndex() {
    if (!indexPromise) {
      indexPromise = (async () => {
        try {
          return {
            themes: await requestRows("loadThemeBracketIndex"),
            usesThemeBracketFiles: true,
          };
        } catch {
          return {
            themes: await requestRows("loadTagIndex"),
            usesThemeBracketFiles: false,
          };
        }
      })();
    }
    return indexPromise;
  }

  function loadSignalRows() {
    if (!signalRowsPromise) {
      signalRowsPromise = (async () => {
        const signalFiles = await Promise.all(
          BRACKET_SIGNAL_TAG_SLUGS.map((slug) => requestRows("loadTagDetail", slug))
        );
        const rowsByCommander = new Map();

        for (const signalRows of signalFiles) {
          for (const row of signalRows) {
            if (!row.commander_slug) continue;
            if (!rowsByCommander.has(row.commander_slug)) {
              rowsByCommander.set(row.commander_slug, []);
            }
            rowsByCommander.get(row.commander_slug).push({
              tag_name: row.tag_name,
              tag_slug: row.tag_slug,
              z: row.z,
              ...(Object.hasOwn(row, "legacy_z") ? { legacy_z: row.legacy_z } : {}),
              tag_decks: row.tag_decks,
              tag_affinity_pct: row.tag_affinity_pct ?? null,
              tag_affinity_adjusted_pct: row.tag_affinity_adjusted_pct ?? null,
              tag_affinity_lower_pct: row.tag_affinity_lower_pct ?? null,
              tag_affinity_upper_pct: row.tag_affinity_upper_pct ?? null,
              affinity_model_status: row.affinity_model_status ?? null,
              affinity_model_version: row.affinity_model_version ?? null,
              rank_within_tag_by_z: row.rank_within_tag_by_z ?? null,
            });
          }
        }

        return rowsByCommander;
      })();
    }
    return signalRowsPromise;
  }

  function loadCommanderSignals(commanderSlug, signals) {
    if (!commanderSignals.has(commanderSlug)) {
      // Ordinary themes previously read commander details, whose ordering
      // determines the deciding tag when signals tie. Preserve that rare case.
      commanderSignals.set(commanderSlug, hasDecidingSignalTie(signals)
        ? requestRows("loadCommanderDetail", commanderSlug)
        : Promise.resolve(signals));
    }
    return commanderSignals.get(commanderSlug);
  }

  async function loadFallbackRows(themeSlug) {
    const allThemeRows = await requestRows("loadTagDetail", themeSlug);
    const qualifiedRows = allThemeRows.filter((row) => qualifiesForTheme(row, themeSlug));
    if (qualifiedRows.length === 0) return [];

    const signalsByCommander = await loadSignalRows();
    const bracketRulesOnly = themeUsesBracketRulesOnly(themeSlug);

    return mapWithConcurrency(qualifiedRows, concurrency, async (row) => {
      const signals = signalsByCommander.get(row.commander_slug) || [];
      const bracketRows = bracketRulesOnly || !row.commander_slug
        ? signals
        : await loadCommanderSignals(row.commander_slug, signals);
      return createThemeRow(row, bracketRows);
    });
  }

  function loadRows(themeSlug, usesThemeBracketFiles = true) {
    const key = `${usesThemeBracketFiles}:${themeSlug}`;
    if (!themeRows.has(key)) {
      themeRows.set(key, (async () => {
        if (usesThemeBracketFiles) {
          try {
            return await requestRows("loadThemeBracketDetail", themeSlug);
          } catch {
            // Older exports and deployments can have only some compact files.
          }
        }
        return loadFallbackRows(themeSlug);
      })());
    }
    return themeRows.get(key);
  }

  return { loadIndex, loadRows };
}
