import { useEffect, useMemo, useState } from "react";
import { loadAffinityRowsWithTrends } from "../lib/api.js";
import {
  filterCommanders,
  groupRowsByCommander,
} from "../lib/commanderUtils.js";
import { formatNumber } from "../lib/formatters.js";
import CommanderCard from "../components/CommanderCard.jsx";
import LoadingState from "../components/LoadingState.jsx";
import ErrorState from "../components/ErrorState.jsx";

/*
  CommanderSearchPage is the first real data-browser prototype.

  It loads the full affinity_rows_with_trends.json file, groups rows by
  commander, and then lets the user search by commander name or slug.

  This is intentionally simpler than the final Chat 10 table work.
*/

const DEFAULT_VISIBLE_COUNT = 24;

export default function CommanderSearchPage() {
  const [rows, setRows] = useState([]);
  const [query, setQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(DEFAULT_VISIBLE_COUNT);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function loadRows() {
      try {
        const loadedRows = await loadAffinityRowsWithTrends();
        setRows(loadedRows);
      } catch (caughtError) {
        setError(caughtError);
      } finally {
        setLoading(false);
      }
    }

    loadRows();
  }, []);

  const commanders = useMemo(() => {
    return groupRowsByCommander(rows);
  }, [rows]);

  const filteredCommanders = useMemo(() => {
    return filterCommanders(commanders, query);
  }, [commanders, query]);

  const visibleCommanders = filteredCommanders.slice(0, visibleCount);

  function handleQueryChange(event) {
    setQuery(event.target.value);
    setVisibleCount(DEFAULT_VISIBLE_COUNT);
  }

  if (loading) {
    return <LoadingState message="Loading commander affinity rows..." />;
  }

  if (error) {
    return <ErrorState title="Could not load commander data" error={error} />;
  }

  return (
    <div className="page">
      <section className="page-header">
        <p className="eyebrow">Commander Search Prototype</p>
        <h1>Search commanders</h1>

        <p>
          This prototype groups the analyzed commander-tag rows into commander
          cards. It shows each commander’s strongest tag affinities by z-score.
        </p>
      </section>

      <section className="search-panel">
        <label htmlFor="commander-search">Commander name or slug</label>

        <input
          id="commander-search"
          type="search"
          value={query}
          onChange={handleQueryChange}
          placeholder="Try Jasmine, Tenth Doctor, Atraxa..."
        />

        <p className="muted">
          Showing {formatNumber(visibleCommanders.length)} of{" "}
          {formatNumber(filteredCommanders.length)} matching commanders. Total
          grouped commanders: {formatNumber(commanders.length)}.
        </p>
      </section>

      {filteredCommanders.length === 0 ? (
        <section className="status-box">
          <h2>No commanders found</h2>
          <p>Try a different spelling or search by commander slug.</p>
        </section>
      ) : (
        <section className="commander-grid">
          {visibleCommanders.map((commander) => (
            <CommanderCard
              key={commander.commander_slug}
              commander={commander}
            />
          ))}
        </section>
      )}

      {visibleCount < filteredCommanders.length ? (
        <div className="load-more-wrap">
          <button
            className="button"
            type="button"
            onClick={() => setVisibleCount((count) => count + 24)}
          >
            Load more commanders
          </button>
        </div>
      ) : null}
    </div>
  );
}