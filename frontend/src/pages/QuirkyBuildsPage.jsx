import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import BracketBadge from "../components/BracketBadge";
import SimpleTable from "../components/SimpleTable";
import {
  formatAdjustedAffinity,
  formatAffinityInterval,
  formatAffinityProbability,
  formatBuildRarity,
  getThemeAffinityMetrics,
} from "../lib/affinityDisplay";
import { BRACKET_OPTIONS } from "../lib/bracketUtils";
import { formatDecimal, formatNumber, formatRank } from "../lib/formatters";
import { readSessionObject, writeSessionValue } from "../lib/persistentState";
import { createQuirkyBuildsDataLoader } from "../lib/quirkyBuildsData";
import {
  DEFAULT_QUIRKY_BUILDS_FILTERS,
  buildQuirkyBuildsRows,
  prepareQuirkyBuildsRows,
} from "../lib/quirkyBuildsUtils";
import { rowMatchesText, sortRows, toggleSortDirection } from "../lib/tableUtils";

const FILTER_STORAGE_KEY = "edhrec-affinity:quirky-builds:filters";
const PAGE_SIZE = 100;

export default function QuirkyBuildsPage() {
  const loaderRef = useRef(null);
  const [filters, setFilters] = useState(() => {
    const stored = readSessionObject(FILTER_STORAGE_KEY, DEFAULT_QUIRKY_BUILDS_FILTERS);
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
  const [state, setState] = useState({ loading: true, error: null });

  useEffect(() => {
    let isCurrent = true;
    loaderRef.current ??= createQuirkyBuildsDataLoader();
    const loader = loaderRef.current;

    async function loadData() {
      setState({ loading: true, error: null });

      try {
        const groups = await loader.loadReport();
        if (!isCurrent) return;
        setData({
          themes: groups.map(({ tag_slug, tag_name }) => ({ tag_slug, tag_name })),
          rows: prepareQuirkyBuildsRows(groups),
        });
        setState({ loading: false, error: null });
      } catch (error) {
        if (isCurrent) {
          setState({ loading: false, error: error.message });
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
    () => buildQuirkyBuildsRows(data.rows, filters),
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
  const minimumRarity = Number(filters.minBuildRarity);
  const maximumShare = Number.isFinite(minimumRarity) && minimumRarity >= 1
    ? formatAffinityProbability(1 / minimumRarity)
    : null;

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
    setFilters({ ...DEFAULT_QUIRKY_BUILDS_FILTERS, brackets: [] });
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
      render: (row) => row.theme_tag_name,
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
      key: "theme_build_rarity", header: "Build Rarity", sortable: true,
      render: (row) => formatBuildRarity(getThemeAffinityMetrics(row)),
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
    <section className="page theme-report-page quirky-builds-page">
      <div className="page-header">
        <p className="eyebrow">Uncommon ways to build</p>
        <h1>Quirky Builds</h1>
        <p>
          Find the rarest observed builds across every theme. Choose the top commanders
          per theme, or rank commanders separately within each bracket for every theme.
        </p>
        <p>
          Build Rarity describes the row’s theme as approximately 1 in N decks
          for this commander, calculated from adjusted affinity. Higher N means
          a less common build: 1 in 50 is an estimated 2% share. Rarity identifies
          off-meta ideas; it does not measure strength or show that a build works.
          Check the commander’s abilities and the observed deck counts when choosing an idea.
        </p>
        <p className="muted">
          Every reported theme can qualify, regardless of its z-score. Missing themes
          are not treated as zero-share builds. Brackets use the existing archetype
          and cEDH signals, with cEDH taking precedence; the theme’s rarity does not
          change the suggested building bracket.
        </p>
        <p>
          Raw and adjusted affinity, the 95% range, z-score, and Rank in Tag
          describe the row’s theme. Rank in Tag compares all reported commanders
          for that theme by z-score; Rank in {perBracket ? "bracket" : "theme"} follows
          this report’s rarity ranking and filters. Bracket Z describes the deciding
          tag used for the suggested building bracket. “Unadjusted” marks a fallback
          and — means unavailable. <Link to="/methodology">How it works</Link>
        </p>
      </div>

      <section className="filter-panel" aria-labelledby="quirky-filters-title">
        <div className="filter-panel-header">
          <div>
            <h2 id="quirky-filters-title">Report options</h2>
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
          <label>
            Minimum Build Rarity (1 in N)
            <input type="number" min="1" step="1" value={filters.minBuildRarity} placeholder="50"
              onChange={(event) => updateFilter("minBuildRarity", event.target.value)} />
            <small className="muted">
              {maximumShare ? `Estimated share of ${maximumShare} or less.` : "Higher N selects rarer builds."}
            </small>
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
        <section className="panel" aria-label="Loading quirky builds report">
          <p className="muted" role="status">Loading quirky builds report…</p>
        </section>
      ) : state.error ? (
        <section className="panel" role="alert">
          <p className="error-message">Could not load the complete quirky builds report: {state.error}</p>
          <button type="button" onClick={() => {
            loaderRef.current = null;
            setRetry((current) => current + 1);
          }}>Retry loading report</button>
        </section>
      ) : (
        <>
          <section className="data-table-section" aria-labelledby="quirky-results-title">
            <div className="table-toolbar">
              <div>
                <p className="eyebrow">Ranked by Build Rarity</p>
                <h2 id="quirky-results-title">{perBracket ? "Rarest builds by theme and bracket" : "Rarest builds by theme"}</h2>
                <p className="table-count" role="status">
                  {formatNumber(rankedRows.length)} results across {formatNumber(representedThemes.size)} of {formatNumber(matchingThemes.length)} matching themes
                </p>
              </div>
              {sort.key && <button type="button" onClick={() => { setSort({ key: "", direction: "desc" }); setPage(1); }}>Restore theme order</button>}
            </div>
            <p className="muted theme-report-note">
              Ranks use the highest Build Rarity within each {perBracket ? "theme and bracket" : "theme"}.
              Ties use theme decks, then total decks, then commander name.
              Only observed builds with a positive adjusted affinity can be ranked.
            </p>
            <SimpleTable columns={columns} rows={pageRows} sortKey={sort.key} sortDirection={sort.direction}
              onSort={handleSort} emptyMessage="No builds match the themes, brackets, and current rarity filters." />
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
              <summary>{formatNumber(emptyThemes.length)} themes have no ranked builds with these filters</summary>
              <p className="muted">{emptyThemes.map((theme) => theme.tag_name || theme.tag_slug).join(" · ")}</p>
            </details>
          )}
        </>
      )}
    </section>
  );
}
