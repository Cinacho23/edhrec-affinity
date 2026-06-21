import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import SimpleTable from "../components/SimpleTable";
import TrendBadge from "../components/TrendBadge";
import { loadTagDetail, loadTagIndex } from "../lib/api";
import {
  formatColorIdentity,
  formatDecimal,
  formatNumber,
  formatPercent,
  formatRank,
} from "../lib/formatters";
import {
  readSessionObject,
  readSessionValue,
  writeSessionValue,
} from "../lib/persistentState";
import {
  passesMax,
  passesMin,
  rowMatchesText,
  sortRows,
  toggleSortDirection,
} from "../lib/tableUtils";
import {
  formatSignedDecimal,
  formatSignedInteger,
  normalizeTrendFields,
} from "../lib/trendUtils";

const DEFAULT_FILTERS = {
  query: "",
  minTotalDecks: "200",
  minTagDecks: "5",
  minZ: "",
  maxZ: "",
  trendStatus: "",
};

const FILTER_STORAGE_KEY = "edhrec-affinity:tag-explorer:filters";
const SELECTED_TAG_STORAGE_KEY = "edhrec-affinity:tag-explorer:selected-tag";

function isAvailableTag(tagList, tagSlug) {
  return tagList.some((tag) => tag.tag_slug === tagSlug);
}

export default function TagExplorerPage() {
  const [tags, setTags] = useState([]);
  const [selectedTag, setSelectedTag] = useState("");
  const [rows, setRows] = useState([]);
  const [filters, setFilters] = useState(() =>
    readSessionObject(FILTER_STORAGE_KEY, DEFAULT_FILTERS)
  );
  const [sortKey, setSortKey] = useState("rank_within_tag_by_z");
  const [sortDirection, setSortDirection] = useState("asc");
  const [state, setState] = useState({
    loadingTags: true,
    loadingRows: false,
    error: null,
  });

  useEffect(() => {
    async function loadTags() {
      try {
        const data = await loadTagIndex();
        const tagList = Array.isArray(data) ? data : [];

        setTags(tagList);
        setSelectedTag((currentTag) => {
          if (currentTag && isAvailableTag(tagList, currentTag)) {
            return currentTag;
          }

          const storedTag = readSessionValue(SELECTED_TAG_STORAGE_KEY, "");

          if (storedTag && isAvailableTag(tagList, storedTag)) {
            return storedTag;
          }

          return tagList[0]?.tag_slug || "";
        });
        setState({ loadingTags: false, loadingRows: false, error: null });
      } catch (error) {
        setState({ loadingTags: false, loadingRows: false, error: error.message });
      }
    }

    loadTags();
  }, []);

  useEffect(() => {
    writeSessionValue(FILTER_STORAGE_KEY, filters);
  }, [filters]);

  useEffect(() => {
    if (selectedTag) {
      writeSessionValue(SELECTED_TAG_STORAGE_KEY, selectedTag);
    }
  }, [selectedTag]);

  useEffect(() => {
    if (!selectedTag) return;

    async function loadRows() {
      setState((previous) => ({
        ...previous,
        loadingRows: true,
        error: null,
      }));

      try {
        const data = await loadTagDetail(selectedTag);
        const normalizedRows = (Array.isArray(data) ? data : []).map(
          normalizeTrendFields
        );
        setRows(normalizedRows);
        setState({ loadingTags: false, loadingRows: false, error: null });
      } catch (error) {
        setState({ loadingTags: false, loadingRows: false, error: error.message });
      }
    }

    loadRows();
  }, [selectedTag]);

  const selectedTagInfo = useMemo(() => {
    return tags.find((tag) => tag.tag_slug === selectedTag);
  }, [tags, selectedTag]);

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
        passesMax(row, "z", filters.maxZ) &&
        (!filters.trendStatus || row.trend_status === filters.trendStatus)
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
    setFilters({ ...DEFAULT_FILTERS });
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
    {
      key: "rank_delta",
      header: "Rank Move",
      sortable: true,
      render: (row) => formatSignedInteger(row.rank_delta),
    },
    {
      key: "z_delta",
      header: "Z \u0394",
      sortable: true,
      render: (row) => formatSignedDecimal(row.z_delta),
    },
    {
      key: "tag_decks_delta",
      header: "Tag Deck \u0394",
      sortable: true,
      render: (row) => formatSignedInteger(row.tag_decks_delta),
    },
    {
      key: "trend_status",
      header: "Trend",
      sortable: true,
      render: (row) => <TrendBadge row={row} compact />,
    },
  ];

  if (state.loadingTags) {
    return (
      <section className="page">
        <p className="muted">Loading tag index…</p>
      </section>
    );
  }

  if (state.error) {
    return (
      <section className="page">
        <h1>Tag Explorer</h1>
        <p className="error-message">Could not load tag data: {state.error}</p>
      </section>
    );
  }

  return (
    <section className="page">
      <div className="page-header">
        <h1>Tag Explorer</h1>
        <p>
          Select a tag to load the complete commander ranking file for that tag.
          Filters and sorting apply to all loaded rows for the selected tag.
        </p>
      </div>

      <section className="tag-selector-panel">
        <label htmlFor="tag-select">
          Tag
          <select
            id="tag-select"
            value={selectedTag}
            onChange={(event) => setSelectedTag(event.target.value)}
          >
            {tags.map((tag) => (
              <option key={tag.tag_slug} value={tag.tag_slug}>
                {tag.tag_name || tag.tag_slug}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section className="summary-card">
        <h2>{selectedTagInfo?.tag_name || selectedTag}</h2>
        <div className="summary-grid">
          <div>
            <span>Rows loaded</span>
            <strong>{formatNumber(rows.length)}</strong>
          </div>
          <div>
            <span>Rows after filters</span>
            <strong>{formatNumber(filteredRows.length)}</strong>
          </div>
          <div>
            <span>Tag slug</span>
            <strong>{selectedTag}</strong>
          </div>
        </div>
      </section>

      <section className="filter-panel">
        <div className="filter-panel-header">
          <div>
            <h2>Filters</h2>
            <p className="muted">Filters apply to the selected tag.</p>
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
              placeholder="Jasmine, Amass, Brims..."
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

          <label>
            Trend status
            <select
              value={filters.trendStatus}
              onChange={(event) => updateFilter("trendStatus", event.target.value)}
            >
              <option value="">Any trend status</option>
              <option value="existing">Existing pair</option>
              <option value="new_pair">New pair</option>
              <option value="removed_pair">Removed pair</option>
              <option value="no_previous_snapshot">No previous snapshot</option>
            </select>
          </label>
        </div>
      </section>

      <section className="data-table-section">
        <div className="table-toolbar">
          <div>
            <p className="eyebrow">Tag rankings</p>
            <h2>{selectedTagInfo?.tag_name || selectedTag}</h2>
          </div>
          <p className="table-count">
            Showing {formatNumber(filteredRows.length)} of {formatNumber(rows.length)} rows
          </p>
        </div>

        {state.loadingRows ? (
          <p className="muted">Loading selected tag…</p>
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
