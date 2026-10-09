import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import BracketBadge from "../components/BracketBadge";
import SimpleTable from "../components/SimpleTable";
import {
  formatAdjustedAffinity,
  formatAffinityInterval,
  formatAffinityProbability,
  getThemeAffinityMetrics,
} from "../lib/affinityDisplay";
import { loadThemeReport } from "../lib/api";
import { BRACKET_OPTIONS } from "../lib/bracketUtils";
import { formatDecimal, formatNumber, formatRank } from "../lib/formatters";
import { readSessionObject, writeSessionValue } from "../lib/persistentState";
import { rowMatchesText, sortRows, toggleSortDirection } from "../lib/tableUtils";
import { createThemeBracketDataLoader, mapWithConcurrency } from "../lib/themeBracketData";
import { expandThemeReport } from "../lib/themeReportData";
import {
  DEFAULT_THEME_REPORT_FILTERS,
  buildThemeReportRows,
  prepareThemeReportRows,
} from "../lib/themeReportUtils";

const FILTER_STORAGE_KEY = "edhrec-affinity:theme-report:filters";
const PAGE_SIZE = 100;

export default function ThemeReportPage() {
  const loaderRef = useRef(null);
  const [filters, setFilters] = useState(() => {
    const stored = readSessionObject(FILTER_STORAGE_KEY, DEFAULT_THEME_REPORT_FILTERS);
    return {
      ...stored,
      brackets: Array.isArray(stored.brackets)
        ? stored.brackets.filter((key) => BRACKET_OPTIONS.some((bracket) => bracket.key === key))
        : [],
    };
  });
  const [sort, setSort] = useState({ key: "", direction: "desc" });
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [data, setData] = useState({ themes: [], rows: [] });
  const [state, setState] = useState({ loading: true, loaded: 0, total: 0, error: null });

  useEffect(() => {
    let isCurrent = true;
    let loadFailed = false;
    loaderRef.current ??= { brackets: createThemeBracketDataLoader(), report: null };
    const loader = loaderRef.current;

    async function loadData() {
      setState({ loading: true, loaded: 0, total: 0, error: null });

      try {
        let groups;

        try {
          // Reuse the in-flight compact request when React remounts the effect.
          loader.report ??= loadThemeReport().then(expandThemeReport);
          groups = await loader.report;
        } catch {
          loader.report = null;
          if (!isCurrent) return;

          const { themes, usesThemeBracketFiles } = await loader.brackets.loadIndex();
          if (!isCurrent) return;

          let loaded = 0;
          setState({ loading: true, loaded, total: themes.length, error: null });
          groups = await mapWithConcurrency(themes, 8, async (theme) => {
            if (!isCurrent || loadFailed) return null;
            const rows = await loader.brackets.loadRows(theme.tag_slug, usesThemeBracketFiles);
            loaded += 1;
            if (isCurrent && !loadFailed && (loaded % 10 === 0 || loaded === themes.length)) {
              setState({ loading: true, loaded, total: themes.length, error: null });
            }
            return { ...theme, rows };
          });
        }

        if (!isCurrent) return;
        setData({
          themes: groups.map(({ tag_slug, tag_name }) => ({ tag_slug, tag_name })),
          rows: prepareThemeReportRows(groups),
        });
        setState({ loading: false, loaded: groups.length, total: groups.length, error: null });
      } catch (error) {
        loadFailed = true;
        if (isCurrent) {
          setState({ loading: false, loaded: 0, total: 0, error: error.message });
        }
      }
    }

    loadData();
    return () => { isCurrent = false; };
  }, [retry]);

  useEffect(() => {
    writeSessionValue(FILTER_STORAGE_KEY, filters);
  }, [filters]);

  const rankedRows = useMemo(
    () => buildThemeReportRows(data.rows, filters),
    [data.rows, filters]
  );
  const sortedRows = useMemo(
    () => sortRows(rankedRows, sort.key, sort.direction),
    [rankedRows, sort]
  );
  const matchingThemes = useMemo(
    () => data.themes.filter((theme) => rowMatchesText(theme, filters.themeQuery, ["tag_name", "tag_slug"])),
    [data.themes, filters.themeQuery]
  );
  const representedThemes = useMemo(
    () => new Set(rankedRows.map((row) => row.theme_tag_slug)),
    [rankedRows]
  );
  const emptyThemes = matchingThemes.filter((theme) => !representedThemes.has(theme.tag_slug));
  const pageCount = Math.max(1, Math.ceil(sortedRows.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pageRows = sortedRows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const perBracket = filters.groupBy === "bracket";

  function updateFilter(key, value) {
    setFilters((current) => ({ ...current, [key]: value }));
    setPage(1);
  }

  function toggleBracket(key) {
    updateFilter("brackets", filters.brackets.includes(key)
      ? filters.brackets.filter((bracket) => bracket !== key)
      : [...filters.brackets, key]);
  }

  function resetFilters() {
    setFilters({ ...DEFAULT_THEME_REPORT_FILTERS, brackets: [] });
    setSort({ key: "", direction: "desc" });
    setPage(1);
  }

  function handleSort(key) {
    setSort((current) => ({
      key,
      direction: toggleSortDirection(current.key, key, current.direction),
    }));
    setPage(1);
  }

  const columns = [
    {
      key: "theme_tag_name", header: "Theme", sortable: true,
      render: (row) => <Link className="table-commander-link" to={`/theme-brackets/${row.theme_tag_slug}`}>{row.theme_tag_name}</Link>,
    },
    {
      key: "report_rank", header: perBracket ? "Rank in bracket" : "Rank in theme",
      render: (row) => <span className="theme-report-rank">#{row.report_rank}</span>,
    },
    {
      key: "commander_name", header: "Commander", sortable: true,
      render: (row) => <Link className="table-commander-link" to={`/commanders/${row.commander_slug}`}>{row.commander_name}</Link>,
    },
    {
      key: "bracket_rank", header: "Bracket", sortable: true,
      render: (row) => <span title={row.bracket_reason}><BracketBadge bracketKey={row.bracket_key} label={row.bracket_label} /></span>,
    },
    {
      key: "theme_affinity_pct", header: "Raw Affinity", sortable: true,
      render: (row) => formatAffinityProbability(getThemeAffinityMetrics(row).tag_affinity_pct),
    },
    {
      key: "theme_affinity_adjusted_pct", header: "Adjusted Affinity", sortable: true,
      render: (row) => formatAdjustedAffinity(getThemeAffinityMetrics(row)),
    },
    {
      key: "theme_affinity_interval", header: "95% Range",
      render: (row) => formatAffinityInterval(getThemeAffinityMetrics(row)),
    },
    {
      key: "theme_affinity_z", header: "Z-Score", sortable: true,
      render: (row) => formatDecimal(getThemeAffinityMetrics(row).z),
    },
    {
      key: "theme_rank_within_tag_by_z", header: "Rank in Tag", sortable: true,
      render: (row) => formatRank(getThemeAffinityMetrics(row).rank_within_tag_by_z),
    },
    {
      key: "theme_tag_decks", header: "Theme Decks", sortable: true,
      render: (row) => formatNumber(row.theme_tag_decks),
    },
    {
      key: "total_decks", header: "Total Decks", sortable: true,
      render: (row) => formatNumber(row.total_decks),
    },
    {
      key: "decision_tag_name", header: "Deciding Tag", sortable: true,
      render: (row) => (
        <span className="theme-report-signal" title={row.bracket_reason}>
          {row.decision_tag_name || "—"}
          {row.decision_z !== null && row.decision_z !== undefined && <small>Bracket Z {formatDecimal(row.decision_z)}</small>}
        </span>
      ),
    },
  ];

  return (
    <section className="page theme-report-page">
      <div className="page-header">
        <p className="eyebrow">Leaders across every theme</p>
        <h1>Theme Report</h1>
        <p>
          Find the highest theme z-scores across all themes. Choose the top commanders
          per theme, or rank commanders separately within each bracket for every theme.
        </p>
        <p className="muted">
          Uses the <Link className="table-commander-link" to="/theme-brackets">Theme Brackets rules</Link>:
          ordinary themes require theme z ≥ 1.05; cEDH, Aggro, Control, Midrange, Tempo,
          and Combo use bracket rules only. cEDH takes precedence when assigning brackets.
          Theme eligibility, bracket suggestions, and rankings use the upgraded
          affinity score with the same numeric cutoffs.
        </p>
        <p>
          Raw and adjusted affinity, the 95% range, z-score, and Rank in Tag
          describe the row’s theme. Rank in Tag compares all reported commanders
          for that theme; Rank in {perBracket ? "bracket" : "theme"} follows
          this report’s filters. Bracket Z describes the deciding tag used for
          the suggested building bracket, our recommended ceiling from tag
          associations. Actual deck strength depends on the build. “Unadjusted”
          marks a fallback and — means
          unavailable. <Link to="/methodology">How it works</Link>
        </p>
      </div>

      <section className="filter-panel" aria-labelledby="report-filters-title">
        <div className="filter-panel-header">
          <div>
            <h2 id="report-filters-title">Report options</h2>
            <p className="muted">Filters apply before the top commanders are selected.</p>
          </div>
          <button type="button" onClick={resetFilters}>Reset filters</button>
        </div>
        <div className="filter-grid theme-report-filters">
          <label>
            Report view
            <select value={filters.groupBy} onChange={(event) => updateFilter("groupBy", event.target.value)}>
              <option value="theme">Top commanders per theme</option>
              <option value="bracket">Top commanders per theme and bracket</option>
            </select>
          </label>
          <label>
            {perBracket ? "Top commanders per theme and bracket" : "Top commanders per theme"}
            <input type="number" min="1" step="1" value={filters.topCount}
              onChange={(event) => updateFilter("topCount", event.target.value)}
              onBlur={() => updateFilter("topCount", String(Math.max(1, Math.floor(Number(filters.topCount) || 1))))}
            />
          </label>
          <label>
            Search themes
            <input type="search" value={filters.themeQuery} placeholder="Mutate, Tokens..."
              onChange={(event) => updateFilter("themeQuery", event.target.value)} />
          </label>
          <label>
            Search commanders or tags
            <input type="search" value={filters.commanderQuery} placeholder="Otrimi, Midrange..."
              onChange={(event) => updateFilter("commanderQuery", event.target.value)} />
          </label>
          <label>
            Minimum total decks
            <input type="number" min="0" value={filters.minTotalDecks} placeholder="200"
              onChange={(event) => updateFilter("minTotalDecks", event.target.value)} />
          </label>
          <label>
            Minimum theme decks
            <input type="number" min="0" value={filters.minThemeDecks} placeholder="5"
              onChange={(event) => updateFilter("minThemeDecks", event.target.value)} />
          </label>
        </div>
        <fieldset className="theme-report-brackets">
          <legend>Brackets to include</legend>
          <p className="muted">Select one or more brackets, or include all brackets.</p>
          <div className="theme-report-bracket-options">
            <button type="button" aria-pressed={filters.brackets.length === 0}
              onClick={() => updateFilter("brackets", [])}>All brackets</button>
            {BRACKET_OPTIONS.map((bracket) => (
              <button key={bracket.key} type="button" aria-pressed={filters.brackets.includes(bracket.key)}
                onClick={() => toggleBracket(bracket.key)}>
                <BracketBadge bracketKey={bracket.key} label={bracket.label} />
              </button>
            ))}
          </div>
        </fieldset>
      </section>

      {state.loading ? (
        <section className="panel" aria-label="Loading report">
          <p className="muted" role="status">
            {state.total > 0
              ? `Loading themes: ${formatNumber(state.loaded)} of ${formatNumber(state.total)}…`
              : "Loading theme report…"}
          </p>
          {state.total > 0 && <progress value={state.loaded} max={state.total} aria-label="Themes loaded" />}
        </section>
      ) : state.error ? (
        <section className="panel" role="alert">
          <p className="error-message">Could not load the complete theme report: {state.error}</p>
          <button type="button" onClick={() => {
            loaderRef.current = null;
            setRetry((current) => current + 1);
          }}>Retry loading report</button>
        </section>
      ) : (
        <>
          <section className="data-table-section" aria-labelledby="theme-report-results-title">
            <div className="table-toolbar">
              <div>
                <p className="eyebrow">Ranked by theme z-score</p>
                <h2 id="theme-report-results-title">{perBracket ? "Leaders by theme and bracket" : "Leaders by theme"}</h2>
                <p className="table-count" role="status">
                  {formatNumber(rankedRows.length)} results across {formatNumber(representedThemes.size)} of {formatNumber(matchingThemes.length)} matching themes
                </p>
              </div>
              {sort.key && <button type="button" onClick={() => { setSort({ key: "", direction: "desc" }); setPage(1); }}>Restore theme order</button>}
            </div>
            <p className="muted theme-report-note">
              Ranks use the highest theme Z within each {perBracket ? "theme and bracket" : "theme"}.
              Ties use theme decks, then total decks, then commander name.
              Only commanders with a numeric theme z-score can be ranked.
            </p>
            <SimpleTable columns={columns} rows={pageRows} sortKey={sort.key} sortDirection={sort.direction}
              onSort={handleSort} emptyMessage="No commanders match the themes, brackets, and current filters." />
            {pageCount > 1 && (
              <nav className="pagination-bar" aria-label="Report pages">
                <span>{formatNumber((currentPage - 1) * PAGE_SIZE + 1)}–{formatNumber(Math.min(currentPage * PAGE_SIZE, sortedRows.length))} of {formatNumber(sortedRows.length)}</span>
                <button type="button" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>Previous</button>
                <span>Page {currentPage} of {pageCount}</span>
                <button type="button" disabled={currentPage === pageCount} onClick={() => setPage(currentPage + 1)}>Next</button>
              </nav>
            )}
          </section>
          {emptyThemes.length > 0 && (
            <details className="panel theme-report-empty-themes">
              <summary>{formatNumber(emptyThemes.length)} themes have no ranked commanders with these filters</summary>
              <p className="muted">{emptyThemes.map((theme) => theme.tag_name || theme.tag_slug).join(" · ")}</p>
            </details>
          )}
        </>
      )}
    </section>
  );
}
