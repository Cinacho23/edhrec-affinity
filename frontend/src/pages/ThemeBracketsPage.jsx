import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import BracketBadge from "../components/BracketBadge";
import SimpleTable from "../components/SimpleTable";
import {
  loadCommanderDetail,
  loadTagDetail,
  loadTagIndex,
  loadThemeBracketDetail,
  loadThemeBracketIndex,
} from "../lib/api";
import {
  ARCHETYPE_TAG_SLUGS,
  BRACKET_OPTIONS,
  CEDH_TAG_SLUG,
  THEME_BRACKET_MIN_Z,
  buildCommanderThemeBracketRows,
  themeUsesBracketRulesOnly,
} from "../lib/bracketUtils";
import {
  formatColorIdentity,
  formatDecimal,
  formatNumber,
} from "../lib/formatters";
import {
  readSessionObject,
  readSessionValue,
  writeSessionValue,
} from "../lib/persistentState";
import {
  passesMin,
  rowMatchesText,
  sortRows,
  toggleSortDirection,
} from "../lib/tableUtils";

const DEFAULT_FILTERS = {
  themeQuery: "",
  commanderQuery: "",
  bracket: "",
  minTotalDecks: "200",
  minThemeDecks: "5",
};

const FILTER_STORAGE_KEY = "edhrec-affinity:theme-brackets:filters";
const SELECTED_THEME_STORAGE_KEY = "edhrec-affinity:theme-brackets:selected-theme";
const SORT_STORAGE_KEY = "edhrec-affinity:theme-brackets:sort";
const COMMANDER_REQUEST_CONCURRENCY = 8;
const BRACKET_SIGNAL_TAG_SLUGS = [
  CEDH_TAG_SLUG,
  ...ARCHETYPE_TAG_SLUGS,
];

function isAvailableTheme(themeList, themeSlug) {
  return themeList.some((theme) => theme.tag_slug === themeSlug);
}

async function mapWithConcurrency(items, concurrency, mapper) {
  const results = new Array(items.length);
  let nextIndex = 0;

  async function worker() {
    while (nextIndex < items.length) {
      const currentIndex = nextIndex;
      nextIndex += 1;
      results[currentIndex] = await mapper(items[currentIndex], currentIndex);
    }
  }

  const workerCount = Math.min(concurrency, Math.max(items.length, 1));
  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return results;
}

function qualifiesForTheme(row, themeSlug) {
  if (themeUsesBracketRulesOnly(themeSlug)) {
    return true;
  }

  const score = Number(row?.z);
  return Number.isFinite(score) && score >= THEME_BRACKET_MIN_Z;
}

function createThemeRow(themeRow, bracketTagRows) {
  return {
    ...themeRow,
    theme_tag_name: themeRow.tag_name,
    theme_tag_slug: themeRow.tag_slug,
    theme_z: themeRow.z,
    theme_tag_decks: themeRow.tag_decks,
    theme_affinity_pct: themeRow.tag_affinity_pct,
    bracket_tag_rows: bracketTagRows,
  };
}

export default function ThemeBracketsPage() {
  const { themeSlug: routeThemeSlug = "" } = useParams();
  const navigate = useNavigate();
  const initialRouteThemeRef = useRef(routeThemeSlug.toLowerCase());
  const commanderDetailCacheRef = useRef(new Map());
  const bracketSignalRowsCacheRef = useRef(null);
  const [themes, setThemes] = useState([]);
  const [selectedTheme, setSelectedTheme] = useState("");
  const [themeRows, setThemeRows] = useState([]);
  const [usesThemeBracketFiles, setUsesThemeBracketFiles] = useState(true);
  const [filters, setFilters] = useState(() =>
    readSessionObject(FILTER_STORAGE_KEY, DEFAULT_FILTERS)
  );
  const [sort, setSort] = useState(() =>
    readSessionObject(SORT_STORAGE_KEY, {
      key: "bracket_rank",
      direction: "desc",
    })
  );
  const [state, setState] = useState({
    loadingThemes: true,
    loadingRows: false,
    error: null,
  });
  const normalizedRouteTheme = routeThemeSlug.toLowerCase();
  const activeTheme = isAvailableTheme(themes, normalizedRouteTheme)
    ? normalizedRouteTheme
    : selectedTheme;

  const loadFallbackRows = useCallback(async (themeSlug) => {
    const tagData = await loadTagDetail(themeSlug);
    const allThemeRows = Array.isArray(tagData) ? tagData : [];
    const qualifiedRows = allThemeRows.filter((row) =>
      qualifiesForTheme(row, themeSlug)
    );

    if (themeUsesBracketRulesOnly(themeSlug)) {
      if (!bracketSignalRowsCacheRef.current) {
        bracketSignalRowsCacheRef.current = (async () => {
          const rowsByCommander = new Map();

          for (const signalSlug of BRACKET_SIGNAL_TAG_SLUGS) {
            const signalData =
              signalSlug === themeSlug
                ? allThemeRows
                : await loadTagDetail(signalSlug);

            for (const signalRow of Array.isArray(signalData) ? signalData : []) {
              const commanderSlug = signalRow.commander_slug;

              if (!commanderSlug) continue;

              if (!rowsByCommander.has(commanderSlug)) {
                rowsByCommander.set(commanderSlug, []);
              }

              rowsByCommander.get(commanderSlug).push({
                tag_name: signalRow.tag_name,
                tag_slug: signalRow.tag_slug,
                z: signalRow.z,
                tag_decks: signalRow.tag_decks,
              });
            }
          }

          return rowsByCommander;
        })();
      }

      const rowsByCommander = await bracketSignalRowsCacheRef.current;

      return qualifiedRows.map((themeRow) =>
        createThemeRow(
          themeRow,
          rowsByCommander.get(themeRow.commander_slug) || []
        )
      );
    }

    return mapWithConcurrency(
      qualifiedRows,
      COMMANDER_REQUEST_CONCURRENCY,
      async (themeRow) => {
        const commanderSlug = themeRow.commander_slug;

        if (!commanderSlug) {
          return createThemeRow(themeRow, []);
        }

        if (!commanderDetailCacheRef.current.has(commanderSlug)) {
          const detailPromise = loadCommanderDetail(commanderSlug).catch(() => []);
          commanderDetailCacheRef.current.set(commanderSlug, detailPromise);
        }

        const detailRows = await commanderDetailCacheRef.current.get(
          commanderSlug
        );

        return createThemeRow(
          themeRow,
          Array.isArray(detailRows) ? detailRows : []
        );
      }
    );
  }, []);

  useEffect(() => {
    async function loadThemes() {
      try {
        let themeList;
        let hasThemeBracketFiles = true;

        try {
          const data = await loadThemeBracketIndex();
          themeList = Array.isArray(data) ? data : [];
        } catch {
          hasThemeBracketFiles = false;
          const data = await loadTagIndex();
          themeList = Array.isArray(data) ? data : [];
        }

        const initialRouteTheme = initialRouteThemeRef.current;
        const storedTheme = readSessionValue(SELECTED_THEME_STORAGE_KEY, "");
        const initialTheme = isAvailableTheme(themeList, initialRouteTheme)
          ? initialRouteTheme
          : isAvailableTheme(themeList, storedTheme)
            ? storedTheme
            : themeList[0]?.tag_slug || "";

        setThemes(themeList);
        setUsesThemeBracketFiles(hasThemeBracketFiles);
        setSelectedTheme(initialTheme);
        setState({ loadingThemes: false, loadingRows: false, error: null });
      } catch (error) {
        setState({
          loadingThemes: false,
          loadingRows: false,
          error: error.message,
        });
      }
    }

    loadThemes();
  }, []);

  useEffect(() => {
    writeSessionValue(FILTER_STORAGE_KEY, filters);
  }, [filters]);

  useEffect(() => {
    writeSessionValue(SORT_STORAGE_KEY, sort);
  }, [sort]);

  useEffect(() => {
    if (activeTheme) {
      writeSessionValue(SELECTED_THEME_STORAGE_KEY, activeTheme);
    }
  }, [activeTheme]);

  useEffect(() => {
    if (!activeTheme) return;
    let isCurrent = true;

    async function loadRows() {
      setState((previous) => ({
        ...previous,
        loadingRows: true,
        error: null,
      }));

      try {
        let nextRows;

        if (usesThemeBracketFiles) {
          try {
            const data = await loadThemeBracketDetail(activeTheme);
            nextRows = Array.isArray(data) ? data : [];
          } catch {
            nextRows = await loadFallbackRows(activeTheme);
          }
        } else {
          nextRows = await loadFallbackRows(activeTheme);
        }

        if (!isCurrent) return;

        setThemeRows(nextRows);
        setState({ loadingThemes: false, loadingRows: false, error: null });
      } catch (error) {
        if (!isCurrent) return;

        setState({
          loadingThemes: false,
          loadingRows: false,
          error: error.message,
        });
      }
    }

    loadRows();

    return () => {
      isCurrent = false;
    };
  }, [activeTheme, usesThemeBracketFiles, loadFallbackRows]);

  const selectedThemeInfo = useMemo(
    () => themes.find((theme) => theme.tag_slug === activeTheme),
    [themes, activeTheme]
  );

  const bracketRows = useMemo(
    () => buildCommanderThemeBracketRows(themeRows, activeTheme),
    [themeRows, activeTheme]
  );

  const eligibleRows = useMemo(
    () =>
      bracketRows.filter(
        (row) =>
          rowMatchesText(row, filters.commanderQuery, [
            "commander_name",
            "commander_slug",
            "decision_tag_name",
          ]) &&
          passesMin(row, "total_decks", filters.minTotalDecks) &&
          passesMin(row, "theme_tag_decks", filters.minThemeDecks)
      ),
    [
      bracketRows,
      filters.commanderQuery,
      filters.minTotalDecks,
      filters.minThemeDecks,
    ]
  );

  const bracketCounts = useMemo(() => {
    const counts = Object.fromEntries(
      BRACKET_OPTIONS.map((bracket) => [bracket.key, 0])
    );

    for (const row of eligibleRows) {
      counts[row.bracket_key] += 1;
    }

    return counts;
  }, [eligibleRows]);

  const filteredThemes = useMemo(
    () =>
      themes.filter((theme) =>
        rowMatchesText(theme, filters.themeQuery, ["tag_name", "tag_slug"])
      ),
    [themes, filters.themeQuery]
  );

  const filteredRows = useMemo(() => {
    const matchingRows = eligibleRows.filter(
      (row) => !filters.bracket || row.bracket_key === filters.bracket
    );

    return sortRows(matchingRows, sort.key, sort.direction);
  }, [eligibleRows, filters.bracket, sort]);

  function selectTheme(themeSlug) {
    setSelectedTheme(themeSlug);
    navigate(`/theme-brackets/${themeSlug}`);
  }

  function updateFilter(key, value) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function resetFilters() {
    setFilters({ ...DEFAULT_FILTERS });
  }

  function handleSort(nextKey) {
    setSort((current) => ({
      key: nextKey,
      direction: toggleSortDirection(current.key, nextKey, current.direction),
    }));
  }

  const columns = [
    {
      key: "commander_name",
      header: "Commander",
      sortable: true,
      render: (row) => (
        <Link className="table-commander-link" to={`/commanders/${row.commander_slug}`}>
          {row.commander_name}
        </Link>
      ),
    },
    {
      key: "bracket_rank",
      header: "Bracket",
      sortable: true,
      render: (row) => (
        <BracketBadge bracketKey={row.bracket_key} label={row.bracket_label} />
      ),
    },
    {
      key: "theme_z",
      header: "Theme Z",
      sortable: true,
      render: (row) => formatDecimal(row.theme_z),
    },
    {
      key: "theme_tag_decks",
      header: "Theme Decks",
      sortable: true,
      render: (row) => formatNumber(row.theme_tag_decks),
    },
    {
      key: "decision_tag_name",
      header: "Deciding Tag",
      sortable: true,
      render: (row) => row.decision_tag_name || "—",
    },
    {
      key: "decision_z",
      header: "Bracket Z",
      sortable: true,
      render: (row) => formatDecimal(row.decision_z),
    },
    {
      key: "bracket_reason",
      header: "Why",
      sortable: false,
      render: (row) => <span className="bracket-reason">{row.bracket_reason}</span>,
    },
    {
      key: "color_identity",
      header: "Colors",
      sortable: true,
      render: (row) => formatColorIdentity(row.color_identity),
    },
    {
      key: "total_decks",
      header: "Total Decks",
      sortable: true,
      render: (row) => formatNumber(row.total_decks),
    },
  ];

  if (state.loadingThemes) {
    return (
      <section className="page">
        <p className="muted">Loading theme brackets...</p>
      </section>
    );
  }

  if (state.error && themes.length === 0) {
    return (
      <section className="page">
        <h1>Theme Brackets</h1>
        <p className="error-message">
          Could not load theme bracket data: {state.error}
        </p>
      </section>
    );
  }

  return (
    <section className="page bracket-page theme-bracket-page">
      <div className="page-header">
        <p className="eyebrow">Theme-level power signals</p>
        <h1>Theme Brackets</h1>
        <p>
          For ordinary themes, commanders must have a theme z-score of at least
          1.05 and satisfy the selected bracket. When the selected theme is
          cEDH or one of the five archetypes, only the bracket rules apply.
        </p>
      </div>

      <section className="bracket-rules panel" aria-labelledby="theme-rules-title">
        <div className="bracket-rules__header">
          <div>
            <p className="eyebrow">Eligibility rules</p>
            <h2 id="theme-rules-title">Two gates, with six exceptions</h2>
          </div>
          <p className="muted">
            All other themes require theme z ≥ 1.05 plus the bracket rule.
            cEDH, Aggro, Control, Midrange, Tempo, and Combo use only the
            bracket rules below. cEDH is always evaluated first.
          </p>
        </div>
        <div className="bracket-rule-grid">
          <div><strong>Theme</strong><span>Ordinary theme z ≥ 1.05, plus bracket</span></div>
          <div><strong>6 tags</strong><span>cEDH/archetypes use bracket rules only</span></div>
          <div><strong>5</strong><span>cEDH ≥ 1.05</span></div>
          <div><strong>4/5</strong><span>cEDH &gt; 0.95 and &lt; 1.05</span></div>
          <div><strong>4</strong><span>cEDH from 0.05 to 0.95</span></div>
          <div><strong>3</strong><span>Any archetype ≥ 1.05</span></div>
          <div><strong>2/3</strong><span>Any archetype &gt; 0.95 and &lt; 1.05</span></div>
          <div><strong>2</strong><span>Any archetype from 0.05 to 0.95</span></div>
          <div><strong>1/2</strong><span>Any archetype &gt; 0 and &lt; 0.05</span></div>
          <div><strong>1</strong><span>No higher threshold met</span></div>
        </div>
      </section>

      <section className="filter-panel">
        <div className="filter-panel-header">
          <div>
            <h2>Filters</h2>
            <p className="muted">Find a theme, commander, deciding tag, or bracket.</p>
          </div>
          <button type="button" onClick={resetFilters}>Reset filters</button>
        </div>
        <div className="filter-grid">
          <label>
            Search themes
            <input
              type="search"
              value={filters.themeQuery}
              onChange={(event) => updateFilter("themeQuery", event.target.value)}
              placeholder="Mutate, Tokens..."
            />
          </label>
          <label>
            Search commanders or tags
            <input
              type="search"
              value={filters.commanderQuery}
              onChange={(event) => updateFilter("commanderQuery", event.target.value)}
              placeholder="Otrimi, Midrange..."
            />
          </label>
          <label>
            Bracket
            <select
              value={filters.bracket}
              onChange={(event) => updateFilter("bracket", event.target.value)}
            >
              <option value="">All brackets</option>
              {BRACKET_OPTIONS.map((bracket) => (
                <option key={bracket.key} value={bracket.key}>{bracket.label}</option>
              ))}
            </select>
          </label>
          <label>
            Minimum total decks
            <input
              type="number"
              min="0"
              value={filters.minTotalDecks}
              onChange={(event) => updateFilter("minTotalDecks", event.target.value)}
              placeholder="200"
            />
          </label>
          <label>
            Minimum theme decks
            <input
              type="number"
              min="0"
              value={filters.minThemeDecks}
              onChange={(event) => updateFilter("minThemeDecks", event.target.value)}
              placeholder="5"
            />
          </label>
        </div>
      </section>

      <section className="set-layout">
        <aside className="set-list-panel theme-list-panel">
          <div className="set-list-header">
            <p className="eyebrow">Themes</p>
            <h2>{formatNumber(filteredThemes.length)} shown</h2>
          </div>
          <div className="set-list" aria-label="Theme list">
            {filteredThemes.length > 0 ? (
              filteredThemes.map((theme) => (
                <button
                  className={
                    theme.tag_slug === activeTheme
                      ? "set-list-button active"
                      : "set-list-button"
                  }
                  key={theme.tag_slug}
                  type="button"
                  onClick={() => selectTheme(theme.tag_slug)}
                >
                  <span>{theme.tag_name || theme.tag_slug}</span>
                  <small>
                    {themeUsesBracketRulesOnly(theme.tag_slug)
                      ? theme.qualified_commander_count !== undefined
                        ? `${formatNumber(theme.qualified_commander_count)} · bracket rules only`
                        : "Bracket rules only"
                      : theme.qualified_commander_count !== undefined
                        ? `${formatNumber(theme.qualified_commander_count)} · z ≥ 1.05`
                        : "Theme z ≥ 1.05, plus bracket"}
                  </small>
                </button>
              ))
            ) : (
              <p className="muted">No themes match the current filter.</p>
            )}
          </div>
        </aside>

        <div className="bracket-results">
          <section className="bracket-summary-grid" aria-label="Bracket counts">
            {BRACKET_OPTIONS.map((bracket) => (
              <button
                className={filters.bracket === bracket.key ? "active" : ""}
                key={bracket.key}
                type="button"
                onClick={() =>
                  updateFilter(
                    "bracket",
                    filters.bracket === bracket.key ? "" : bracket.key
                  )
                }
              >
                <BracketBadge bracketKey={bracket.key} label={bracket.label} />
                <strong>{formatNumber(bracketCounts[bracket.key])}</strong>
              </button>
            ))}
          </section>

          <section className="data-table-section set-results-panel">
            <div className="table-toolbar">
              <div>
                <p className="eyebrow">Eligible commanders</p>
                <h2>{selectedThemeInfo?.tag_name || activeTheme || "No theme selected"}</h2>
              </div>
              <p className="table-count">
                Showing {formatNumber(filteredRows.length)} of {formatNumber(bracketRows.length)} commanders
              </p>
            </div>

            {state.loadingRows ? (
              <p className="muted set-table-message">
                Loading and classifying theme commanders...
              </p>
            ) : state.error ? (
              <p className="error-message set-table-message">
                Could not load this theme: {state.error}
              </p>
            ) : activeTheme ? (
              <SimpleTable
                columns={columns}
                rows={filteredRows}
                emptyMessage="No commanders match the theme, bracket, and current filters."
                sortKey={sort.key}
                sortDirection={sort.direction}
                onSort={handleSort}
              />
            ) : (
              <p className="muted set-table-message">No themes found.</p>
            )}
          </section>
        </div>
      </section>
    </section>
  );
}
