import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import SimpleTable from "../components/SimpleTable";
import {
  loadCommanderDetail,
  loadCommanderIndex,
  loadSetDetail,
  loadSetIndex,
} from "../lib/api";
import {
  formatColorIdentity,
  formatDecimal,
  formatNumber,
  formatPercent,
} from "../lib/formatters";
import {
  readSessionObject,
  readSessionValue,
  writeSessionValue,
} from "../lib/persistentState";
import {
  buildSetIndexFromCommanders,
  formatOriginSets,
  getCommandersForSet,
  getCommanderOriginSets,
  rowBelongsToSet,
  rowHasTagMetrics,
} from "../lib/setUtils";
import {
  passesMax,
  passesMin,
  rowMatchesText,
  sortRows,
  toggleSortDirection,
} from "../lib/tableUtils";

const DEFAULT_FILTERS = {
  setQuery: "",
  commanderQuery: "",
  tagQuery: "",
  minTotalDecks: "200",
  minTagDecks: "5",
  minZ: "",
  maxZ: "",
};

const FILTER_STORAGE_KEY = "edhrec-affinity:set-explorer:filters";
const SELECTED_SET_STORAGE_KEY = "edhrec-affinity:set-explorer:selected-set";
const SORT_STORAGE_KEY = "edhrec-affinity:set-explorer:sort:v2";

function isAvailableSet(setList, setCode) {
  return setList.some((setInfo) => setInfo.set_code === setCode);
}

function getSelectedOriginSet(row, setCode) {
  const normalizedSetCode = String(setCode || "").toLowerCase();

  return getCommanderOriginSets(row).find(
    (originSet) => originSet.set_code === normalizedSetCode
  );
}

export default function SetExplorerPage() {
  const commanderDetailCacheRef = useRef(new Map());
  const [sets, setSets] = useState([]);
  const [commanders, setCommanders] = useState([]);
  const [selectedSet, setSelectedSet] = useState(() =>
    readSessionValue(SELECTED_SET_STORAGE_KEY, "")
  );
  const [rows, setRows] = useState([]);
  const [filters, setFilters] = useState(() =>
    readSessionObject(FILTER_STORAGE_KEY, DEFAULT_FILTERS)
  );
  const [sort, setSort] = useState(() =>
    readSessionObject(SORT_STORAGE_KEY, {
      key: "z",
      direction: "desc",
    })
  );
  const [usesExportedSetFiles, setUsesExportedSetFiles] = useState(true);
  const [state, setState] = useState({
    loadingSets: true,
    loadingRows: false,
    error: null,
  });

  const expandSetRowsWithCommanderDetails = useCallback(async (setRows, setCode) => {
    const baseRows = Array.isArray(setRows) ? setRows : [];

    if (baseRows.length === 0 || baseRows.some(rowHasTagMetrics)) {
      return baseRows;
    }

    const originBySlug = new Map(
      baseRows
        .filter((row) => row.commander_slug)
        .map((row) => [row.commander_slug, getSelectedOriginSet(row, setCode)])
    );
    const commanderSlugs = [...originBySlug.keys()];
    const expandedRows = [];

    await Promise.all(
      commanderSlugs.map(async (commanderSlug) => {
        if (!commanderDetailCacheRef.current.has(commanderSlug)) {
          try {
            const detailRows = await loadCommanderDetail(commanderSlug);
            commanderDetailCacheRef.current.set(
              commanderSlug,
              Array.isArray(detailRows) ? detailRows : []
            );
          } catch {
            commanderDetailCacheRef.current.set(commanderSlug, []);
          }
        }

        const originSet = originBySlug.get(commanderSlug);
        const detailRows = commanderDetailCacheRef.current.get(commanderSlug) || [];

        for (const detailRow of detailRows) {
          if (!rowBelongsToSet(detailRow, setCode) && !originSet) {
            continue;
          }

          expandedRows.push({
            ...detailRow,
            origin_set_code: originSet?.set_code || detailRow.origin_set_code,
            origin_set_name: originSet?.set_name || detailRow.origin_set_name,
            origin_released_at:
              originSet?.released_at || detailRow.origin_released_at,
            scryfall_set_uri:
              originSet?.scryfall_set_uri || detailRow.scryfall_set_uri,
            set_uri: originSet?.set_uri || detailRow.set_uri,
          });
        }
      })
    );

    return expandedRows.length > 0 ? expandedRows : baseRows;
  }, []);

  useEffect(() => {
    async function loadData() {
      try {
        const commanderIndex = await loadCommanderIndex();
        const commanderList = Array.isArray(commanderIndex) ? commanderIndex : [];
        let setList = [];
        let hasSetFiles = true;

        try {
          const setIndex = await loadSetIndex();
          setList = Array.isArray(setIndex) ? setIndex : [];
        } catch {
          hasSetFiles = false;
          setList = buildSetIndexFromCommanders(commanderList);
        }

        setCommanders(commanderList);
        setSets(setList);
        setUsesExportedSetFiles(hasSetFiles);
        setSelectedSet((currentSet) => {
          if (currentSet && isAvailableSet(setList, currentSet)) {
            return currentSet;
          }

          const storedSet = readSessionValue(SELECTED_SET_STORAGE_KEY, "");

          if (storedSet && isAvailableSet(setList, storedSet)) {
            return storedSet;
          }

          return setList[0]?.set_code || "";
        });
        setState({ loadingSets: false, loadingRows: false, error: null });
      } catch (error) {
        setState({ loadingSets: false, loadingRows: false, error: error.message });
      }
    }

    loadData();
  }, []);

  useEffect(() => {
    writeSessionValue(FILTER_STORAGE_KEY, filters);
  }, [filters]);

  useEffect(() => {
    writeSessionValue(SORT_STORAGE_KEY, sort);
  }, [sort]);

  useEffect(() => {
    if (selectedSet) {
      writeSessionValue(SELECTED_SET_STORAGE_KEY, selectedSet);
    }
  }, [selectedSet]);

  useEffect(() => {
    if (!selectedSet) return;
    let isCurrent = true;

    async function loadRows() {
      setState((previous) => ({
        ...previous,
        loadingRows: true,
        error: null,
      }));

      try {
        let nextRows = [];

        if (usesExportedSetFiles) {
          try {
            const data = await loadSetDetail(selectedSet);
            nextRows = Array.isArray(data) ? data : [];
          } catch {
            nextRows = getCommandersForSet(commanders, selectedSet);
          }
        } else {
          nextRows = getCommandersForSet(commanders, selectedSet);
        }

        nextRows = await expandSetRowsWithCommanderDetails(nextRows, selectedSet);

        if (!isCurrent) {
          return;
        }

        setRows(nextRows);
        setState({ loadingSets: false, loadingRows: false, error: null });
      } catch (error) {
        if (!isCurrent) {
          return;
        }

        setState({ loadingSets: false, loadingRows: false, error: error.message });
      }
    }

    loadRows();

    return () => {
      isCurrent = false;
    };
  }, [
    selectedSet,
    commanders,
    usesExportedSetFiles,
    expandSetRowsWithCommanderDetails,
  ]);

  const selectedSetInfo = useMemo(() => {
    return sets.find((setInfo) => setInfo.set_code === selectedSet);
  }, [sets, selectedSet]);

  const filteredSets = useMemo(() => {
    return sets.filter((setInfo) =>
      rowMatchesText(setInfo, filters.setQuery, ["set_name", "set_code"])
    );
  }, [sets, filters.setQuery]);

  const filteredRows = useMemo(() => {
    const filtered = rows.filter((row) => {
      return (
        rowMatchesText(row, filters.commanderQuery, [
          "commander_name",
          "commander_slug",
          "origin_set_name",
          "origin_set_code",
        ]) &&
        rowMatchesText(row, filters.tagQuery, ["tag_name", "tag_slug"]) &&
        passesMin(row, "total_decks", filters.minTotalDecks) &&
        passesMin(row, "tag_decks", filters.minTagDecks) &&
        passesMin(row, "z", filters.minZ) &&
        passesMax(row, "z", filters.maxZ)
      );
    });

    return sortRows(filtered, sort.key, sort.direction);
  }, [
    rows,
    filters.commanderQuery,
    filters.tagQuery,
    filters.minTotalDecks,
    filters.minTagDecks,
    filters.minZ,
    filters.maxZ,
    sort,
  ]);

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
      key: "color_identity",
      header: "Colors",
      sortable: true,
      render: (row) => formatColorIdentity(row.color_identity),
    },
    {
      key: "tag_name",
      header: "Tag",
      sortable: true,
      render: (row) => row.tag_name || "-",
    },
    {
      key: "z",
      header: "Z-Score",
      sortable: true,
      render: (row) => formatDecimal(row.z),
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
      key: "origin_set_name",
      header: "Origin Set",
      sortable: true,
      render: (row) => row.origin_set_name || formatOriginSets(row),
    },
  ];

  if (state.loadingSets) {
    return (
      <section className="page">
        <p className="muted">Loading set index...</p>
      </section>
    );
  }

  if (state.error) {
    return (
      <section className="page">
        <h1>Set Explorer</h1>
        <p className="error-message">Could not load set data: {state.error}</p>
      </section>
    );
  }

  return (
    <section className="page">
      <div className="page-header">
        <h1>Set Explorer</h1>
        <p>
          Browse Magic sets, then inspect commander/tag rows for cards that
          originated in the selected set.
        </p>
      </div>

      <section className="filter-panel">
        <div className="filter-panel-header">
          <div>
            <h2>Filters</h2>
            <p className="muted">
              Filter the set list, commanders, or tags in the selected set.
            </p>
          </div>

          <button type="button" onClick={resetFilters}>
            Reset filters
          </button>
        </div>

        <div className="filter-grid">
          <label>
            Search sets
            <input
              type="search"
              value={filters.setQuery}
              onChange={(event) => updateFilter("setQuery", event.target.value)}
              placeholder="Kaldheim, KHM..."
            />
          </label>

          <label>
            Search commanders
            <input
              type="search"
              value={filters.commanderQuery}
              onChange={(event) =>
                updateFilter("commanderQuery", event.target.value)
              }
              placeholder="Jorn, Atraxa..."
            />
          </label>

          <label>
            Search tags
            <input
              type="search"
              value={filters.tagQuery}
              onChange={(event) => updateFilter("tagQuery", event.target.value)}
              placeholder="Snow, tokens, artifacts..."
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
              placeholder="Optional"
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

      <section className="set-layout">
        <aside className="set-list-panel">
          <div className="set-list-header">
            <div>
              <p className="eyebrow">Sets</p>
              <h2>{formatNumber(filteredSets.length)} shown</h2>
            </div>
          </div>

          <div className="set-list" aria-label="Set list">
            {filteredSets.length > 0 ? (
              filteredSets.map((setInfo) => (
                <button
                  className={
                    setInfo.set_code === selectedSet
                      ? "set-list-button active"
                      : "set-list-button"
                  }
                  key={setInfo.set_code}
                  type="button"
                  onClick={() => setSelectedSet(setInfo.set_code)}
                >
                  <span>{setInfo.set_name || setInfo.set_code.toUpperCase()}</span>
                  <small>
                    {setInfo.set_code.toUpperCase()} -{" "}
                    {formatNumber(setInfo.commander_count || 0)} commanders
                  </small>
                </button>
              ))
            ) : (
              <p className="muted">No sets match the current filter.</p>
            )}
          </div>
        </aside>

        <section className="data-table-section set-results-panel">
          <div className="table-toolbar">
            <div>
              <p className="eyebrow">Set commanders</p>
              <h2>{selectedSetInfo?.set_name || selectedSet || "No set selected"}</h2>
            </div>
            <p className="table-count">
              Showing {formatNumber(filteredRows.length)} of{" "}
              {formatNumber(rows.length)} rows
            </p>
          </div>

          {state.loadingRows ? (
            <p className="muted set-table-message">Loading selected set...</p>
          ) : selectedSet ? (
            <SimpleTable
              columns={columns}
              rows={filteredRows}
              emptyMessage="No commander/tag rows found for this set."
              sortKey={sort.key}
              sortDirection={sort.direction}
              onSort={handleSort}
            />
          ) : (
            <p className="muted set-table-message">No sets found.</p>
          )}
        </section>
      </section>
    </section>
  );
}
