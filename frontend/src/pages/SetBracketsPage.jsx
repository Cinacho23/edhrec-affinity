import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import BracketBadge from "../components/BracketBadge";
import SimpleTable from "../components/SimpleTable";
import {
  loadCommanderDetail,
  loadCommanderIndex,
  loadSetDetail,
  loadSetIndex,
} from "../lib/api";
import {
  BRACKET_OPTIONS,
  buildCommanderBracketRows,
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
  buildSetIndexFromCommanders,
  getCommandersForSet,
  getCommanderOriginSets,
  rowHasTagMetrics,
} from "../lib/setUtils";
import {
  rowMatchesText,
  sortRows,
  toggleSortDirection,
} from "../lib/tableUtils";

const DEFAULT_FILTERS = {
  setQuery: "",
  commanderQuery: "",
  bracket: "",
};

const FILTER_STORAGE_KEY = "edhrec-affinity:set-brackets:filters";
const SELECTED_SET_STORAGE_KEY = "edhrec-affinity:set-brackets:selected-set";
const SORT_STORAGE_KEY = "edhrec-affinity:set-brackets:sort";

function isAvailableSet(setList, setCode) {
  return setList.some((setInfo) => setInfo.set_code === setCode);
}

function getSelectedOriginSet(row, setCode) {
  const normalizedSetCode = String(setCode || "").toLowerCase();

  return getCommanderOriginSets(row).find(
    (originSet) => originSet.set_code === normalizedSetCode
  );
}

export default function SetBracketsPage() {
  const { setCode: routeSetCode = "" } = useParams();
  const navigate = useNavigate();
  const initialRouteSetCodeRef = useRef(routeSetCode.toLowerCase());
  const commanderDetailCacheRef = useRef(new Map());
  const [sets, setSets] = useState([]);
  const [commanders, setCommanders] = useState([]);
  const [selectedSet, setSelectedSet] = useState("");
  const [tagRows, setTagRows] = useState([]);
  const [filters, setFilters] = useState(() =>
    readSessionObject(FILTER_STORAGE_KEY, DEFAULT_FILTERS)
  );
  const [sort, setSort] = useState(() =>
    readSessionObject(SORT_STORAGE_KEY, {
      key: "bracket_rank",
      direction: "desc",
    })
  );
  const [usesExportedSetFiles, setUsesExportedSetFiles] = useState(true);
  const [state, setState] = useState({
    loadingSets: true,
    loadingRows: false,
    error: null,
  });
  const normalizedRouteSet = routeSetCode.toLowerCase();
  const activeSet = isAvailableSet(sets, normalizedRouteSet)
    ? normalizedRouteSet
    : selectedSet;

  const expandRowsWithCommanderDetails = useCallback(async (setRows, setCode) => {
    const baseRows = Array.isArray(setRows) ? setRows : [];

    if (baseRows.length === 0 || baseRows.some(rowHasTagMetrics)) {
      return baseRows;
    }

    const expandedGroups = await Promise.all(
      baseRows.map(async (commander) => {
        const commanderSlug = commander.commander_slug;
        const originSet = getSelectedOriginSet(commander, setCode);

        if (!commanderSlug) {
          return [commander];
        }

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

        const detailRows = commanderDetailCacheRef.current.get(commanderSlug) || [];

        if (detailRows.length === 0) {
          return [commander];
        }

        return detailRows.map((detailRow) => ({
          ...detailRow,
          origin_set_code: originSet?.set_code || detailRow.origin_set_code,
          origin_set_name: originSet?.set_name || detailRow.origin_set_name,
          origin_released_at:
            originSet?.released_at || detailRow.origin_released_at,
        }));
      })
    );

    return expandedGroups.flat();
  }, []);

  useEffect(() => {
    async function loadData() {
      try {
        const commanderIndex = await loadCommanderIndex();
        const commanderList = Array.isArray(commanderIndex) ? commanderIndex : [];
        let setList;
        let hasSetFiles = true;

        try {
          const setIndex = await loadSetIndex();
          setList = Array.isArray(setIndex) ? setIndex : [];
        } catch {
          hasSetFiles = false;
          setList = buildSetIndexFromCommanders(commanderList);
        }

        const initialRouteSet = initialRouteSetCodeRef.current;
        const storedSet = readSessionValue(SELECTED_SET_STORAGE_KEY, "");
        const initialSet = isAvailableSet(setList, initialRouteSet)
          ? initialRouteSet
          : isAvailableSet(setList, storedSet)
            ? storedSet
            : setList[0]?.set_code || "";

        setCommanders(commanderList);
        setSets(setList);
        setUsesExportedSetFiles(hasSetFiles);
        setSelectedSet(initialSet);
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
    if (activeSet) {
      writeSessionValue(SELECTED_SET_STORAGE_KEY, activeSet);
    }
  }, [activeSet]);

  useEffect(() => {
    if (!activeSet) return;
    let isCurrent = true;

    async function loadRows() {
      setState((previous) => ({
        ...previous,
        loadingRows: true,
        error: null,
      }));

      try {
        let nextRows;

        if (usesExportedSetFiles) {
          try {
            const data = await loadSetDetail(activeSet);
            nextRows = Array.isArray(data) ? data : [];
          } catch {
            nextRows = getCommandersForSet(commanders, activeSet);
          }
        } else {
          nextRows = getCommandersForSet(commanders, activeSet);
        }

        nextRows = await expandRowsWithCommanderDetails(nextRows, activeSet);

        if (!isCurrent) return;

        setTagRows(nextRows);
        setState({ loadingSets: false, loadingRows: false, error: null });
      } catch (error) {
        if (!isCurrent) return;

        setState({ loadingSets: false, loadingRows: false, error: error.message });
      }
    }

    loadRows();

    return () => {
      isCurrent = false;
    };
  }, [
    activeSet,
    commanders,
    usesExportedSetFiles,
    expandRowsWithCommanderDetails,
  ]);

  const selectedSetInfo = useMemo(
    () => sets.find((setInfo) => setInfo.set_code === activeSet),
    [sets, activeSet]
  );

  const bracketRows = useMemo(
    () => buildCommanderBracketRows(tagRows),
    [tagRows]
  );

  const bracketCounts = useMemo(() => {
    const counts = Object.fromEntries(
      BRACKET_OPTIONS.map((bracket) => [bracket.key, 0])
    );

    for (const row of bracketRows) {
      counts[row.bracket_key] += 1;
    }

    return counts;
  }, [bracketRows]);

  const filteredSets = useMemo(
    () =>
      sets.filter((setInfo) =>
        rowMatchesText(setInfo, filters.setQuery, ["set_name", "set_code"])
      ),
    [sets, filters.setQuery]
  );

  const filteredRows = useMemo(() => {
    const matchingRows = bracketRows.filter(
      (row) =>
        rowMatchesText(row, filters.commanderQuery, [
          "commander_name",
          "commander_slug",
          "decision_tag_name",
        ]) && (!filters.bracket || row.bracket_key === filters.bracket)
    );

    return sortRows(matchingRows, sort.key, sort.direction);
  }, [bracketRows, filters.commanderQuery, filters.bracket, sort]);

  function selectSet(setCode) {
    setSelectedSet(setCode);
    navigate(`/brackets/${setCode}`);
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
      key: "decision_tag_name",
      header: "Deciding Tag",
      sortable: true,
      render: (row) => row.decision_tag_name || "—",
    },
    {
      key: "decision_z",
      header: "Z-Score",
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

  if (state.loadingSets) {
    return (
      <section className="page">
        <p className="muted">Loading set brackets...</p>
      </section>
    );
  }

  if (state.error && sets.length === 0) {
    return (
      <section className="page">
        <h1>Commander Brackets</h1>
        <p className="error-message">Could not load bracket data: {state.error}</p>
      </section>
    );
  }

  return (
    <section className="page bracket-page">
      <div className="page-header">
        <p className="eyebrow">Set-level power signals</p>
        <h1>Commander Brackets</h1>
        <p>
          Choose a Magic set to classify each of its commanders from Bracket 1
          through Bracket 5. In-flux labels identify commanders whose deciding
          z-score sits between thresholds.
        </p>
      </div>

      <section className="bracket-rules panel" aria-labelledby="bracket-rules-title">
        <div className="bracket-rules__header">
          <div>
            <p className="eyebrow">Classification order</p>
            <h2 id="bracket-rules-title">cEDH takes precedence</h2>
          </div>
          <p className="muted">
            Aggro, Control, Midrange, Tempo, and Combo are evaluated only after
            cEDH does not qualify for Bracket 4 or higher.
          </p>
        </div>
        <div className="bracket-rule-grid">
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
            <p className="muted">Find a set, commander, deciding tag, or bracket.</p>
          </div>
          <button type="button" onClick={resetFilters}>Reset filters</button>
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
            Search commanders or tags
            <input
              type="search"
              value={filters.commanderQuery}
              onChange={(event) => updateFilter("commanderQuery", event.target.value)}
              placeholder="Jorn, Midrange..."
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
        </div>
      </section>

      <section className="set-layout">
        <aside className="set-list-panel">
          <div className="set-list-header">
            <p className="eyebrow">Sets</p>
            <h2>{formatNumber(filteredSets.length)} shown</h2>
          </div>
          <div className="set-list" aria-label="Set list">
            {filteredSets.length > 0 ? (
              filteredSets.map((setInfo) => (
                <button
                  className={
                    setInfo.set_code === activeSet
                      ? "set-list-button active"
                      : "set-list-button"
                  }
                  key={setInfo.set_code}
                  type="button"
                  onClick={() => selectSet(setInfo.set_code)}
                >
                  <span>{setInfo.set_name || setInfo.set_code.toUpperCase()}</span>
                  <small>
                    {setInfo.set_code.toUpperCase()} · {formatNumber(setInfo.commander_count || 0)} commanders
                  </small>
                </button>
              ))
            ) : (
              <p className="muted">No sets match the current filter.</p>
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
                <p className="eyebrow">Set commanders</p>
                <h2>{selectedSetInfo?.set_name || activeSet || "No set selected"}</h2>
              </div>
              <p className="table-count">
                Showing {formatNumber(filteredRows.length)} of {formatNumber(bracketRows.length)} commanders
              </p>
            </div>

            {state.loadingRows ? (
              <p className="muted set-table-message">Loading and classifying commanders...</p>
            ) : state.error ? (
              <p className="error-message set-table-message">Could not load this set: {state.error}</p>
            ) : activeSet ? (
              <SimpleTable
                columns={columns}
                rows={filteredRows}
                emptyMessage="No commanders match the current filters."
                sortKey={sort.key}
                sortDirection={sort.direction}
                onSort={handleSort}
              />
            ) : (
              <p className="muted set-table-message">No sets found.</p>
            )}
          </section>
        </div>
      </section>
    </section>
  );
}
