import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import SimpleTable from "../components/SimpleTable";
import { loadLeaderboardIndex, loadLeaderboardPage } from "../lib/api";
import {
  formatDecimal,
  formatNumber,
  formatPercent,
  formatRank,
} from "../lib/formatters";
import {
  passesMax,
  passesMin,
  rowMatchesText,
  sortRows,
  toggleSortDirection,
} from "../lib/tableUtils";

const DEFAULT_FILTERS = {
  query: "",
  minTotalDecks: "",
  minTagDecks: "",
  minZ: "",
  maxZ: "",
};

export default function GlobalLeaderboardPage() {
  const [index, setIndex] = useState(null);
  const [rows, setRows] = useState([]);
  const [pageNumber, setPageNumber] = useState(1);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [sortKey, setSortKey] = useState("z");
  const [sortDirection, setSortDirection] = useState("desc");
  const [state, setState] = useState({
    loadingIndex: true,
    loadingRows: false,
    error: null,
  });

  useEffect(() => {
    async function loadIndex() {
      try {
        const data = await loadLeaderboardIndex();
        setIndex(data);
        setState({ loadingIndex: false, loadingRows: false, error: null });
      } catch (error) {
        setState({ loadingIndex: false, loadingRows: false, error: error.message });
      }
    }

    loadIndex();
  }, []);

  useEffect(() => {
    if (!index) return;

    async function loadRows() {
      setState((previous) => ({
        ...previous,
        loadingRows: true,
        error: null,
      }));

      try {
        const data = await loadLeaderboardPage(pageNumber);
        setRows(Array.isArray(data) ? data : []);
        setState({ loadingIndex: false, loadingRows: false, error: null });
      } catch (error) {
        setState({ loadingIndex: false, loadingRows: false, error: error.message });
      }
    }

    loadRows();
  }, [index, pageNumber]);

  const filteredRows = useMemo(() => {
    const filtered = rows.filter((row) => {
      return (
        rowMatchesText(row, filters.query, [
          "commander_name",
          "commander_slug",
          "tag_name",
          "tag_slug",
        ]) &&
        passesMin(row, "total_decks", filters.minTotalDecks) &&
        passesMin(row, "tag_decks", filters.minTagDecks) &&
        passesMin(row, "z", filters.minZ) &&
        passesMax(row, "z", filters.maxZ)
      );
    });

    return sortRows(filtered, sortKey, sortDirection);
  }, [rows, filters, sortKey, sortDirection]);

  function updateFilter(key, value) {
    setFilters((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function resetFilters() {
    setFilters(DEFAULT_FILTERS);
  }

  function handleSort(nextKey) {
    setSortDirection((currentDirection) =>
      toggleSortDirection(sortKey, nextKey, currentDirection)
    );
    setSortKey(nextKey);
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
      key: "tag_name",
      header: "Tag",
      sortable: true,
      render: (row) => row.tag_name ?? "—",
    },
    {
      key: "total_decks",
      header: "Total Decks",
      sortable: true,
      render: (row) => formatNumber(row.total_decks),
    },
    {
      key: "tag_decks",
      header: "Tag Decks",
      sortable: true,
      render: (row) => formatNumber(row.tag_decks),
    },
    {
      key: "tag_affinity_pct",
      header: "Affinity",
      sortable: true,
      render: (row) => formatPercent(row.tag_affinity_pct),
    },
    {
      key: "z",
      header: "Z-Score",
      sortable: true,
      render: (row) => formatDecimal(row.z),
    },
    {
      key: "rank_within_tag_by_z",
      header: "Rank",
      sortable: true,
      render: (row) => formatRank(row.rank_within_tag_by_z),
    },
  ];

  if (state.loadingIndex) {
    return (
      <section className="page">
        <p className="muted">Loading leaderboard index…</p>
      </section>
    );
  }

  if (state.error) {
    return (
      <section className="page">
        <h1>Global Leaderboard</h1>
        <p className="error-message">Could not load leaderboard: {state.error}</p>
      </section>
    );
  }

  const pageCount = index?.page_count || 1;

  return (
    <section className="page">
      <div className="page-header">
        <h1>Global Leaderboard</h1>
        <p>
          The leaderboard is served in static shards. Filters and sorting apply to
          the currently loaded leaderboard page.
        </p>
      </div>

      <section className="panel">
        <dl className="summary-list">
          <div>
            <dt>Total rows</dt>
            <dd>{formatNumber(index?.total_rows)}</dd>
          </div>

          <div>
            <dt>Page size</dt>
            <dd>{formatNumber(index?.page_size)}</dd>
          </div>

          <div>
            <dt>Current page</dt>
            <dd>
              {formatNumber(pageNumber)} of {formatNumber(pageCount)}
            </dd>
          </div>
        </dl>

        <div className="pagination-bar">
          <button
            type="button"
            disabled={pageNumber <= 1}
            onClick={() => setPageNumber((current) => Math.max(1, current - 1))}
          >
            Previous
          </button>

          <button
            type="button"
            disabled={pageNumber >= pageCount}
            onClick={() => setPageNumber((current) => Math.min(pageCount, current + 1))}
          >
            Next
          </button>
        </div>
      </section>

      <section className="filter-panel">
        <div className="filter-panel-header">
          <div>
            <h2>Filters</h2>
            <p className="muted">Filters apply to the current leaderboard page.</p>
          </div>

          <button type="button" onClick={resetFilters}>
            Reset filters
          </button>
        </div>

        <div className="filter-grid">
          <label>
            Search commander or tag
            <input
              type="search"
              value={filters.query}
              onChange={(event) => updateFilter("query", event.target.value)}
              placeholder="Jasmine, cEDH, tokens…"
            />
          </label>

          <label>
            Minimum total decks
            <input
              type="number"
              value={filters.minTotalDecks}
              onChange={(event) => updateFilter("minTotalDecks", event.target.value)}
              placeholder="200"
            />
          </label>

          <label>
            Minimum tag decks
            <input
              type="number"
              value={filters.minTagDecks}
              onChange={(event) => updateFilter("minTagDecks", event.target.value)}
              placeholder="5"
            />
          </label>

          <label>
            Minimum z-score
            <input
              type="number"
              step="0.1"
              value={filters.minZ}
              onChange={(event) => updateFilter("minZ", event.target.value)}
              placeholder="0"
            />
          </label>

          <label>
            Maximum z-score
            <input
              type="number"
              step="0.1"
              value={filters.maxZ}
              onChange={(event) => updateFilter("maxZ", event.target.value)}
              placeholder="Optional"
            />
          </label>
        </div>
      </section>

      <section className="data-table-section">
        <div className="table-toolbar">
          <div>
            <p className="eyebrow">Leaderboard</p>
            <h2>Page {formatNumber(pageNumber)}</h2>
          </div>
          <p className="table-count">
            Showing {formatNumber(filteredRows.length)} of {formatNumber(rows.length)} rows
          </p>
        </div>

        {state.loadingRows ? (
          <p className="muted">Loading leaderboard page…</p>
        ) : (
          <SimpleTable
            columns={columns}
            rows={filteredRows}
            sortKey={sortKey}
            sortDirection={sortDirection}
            onSort={handleSort}
          />
        )}
      </section>
    </section>
  );
}