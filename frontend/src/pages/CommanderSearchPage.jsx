import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import CommanderImageGallery from "../components/CommanderImageGallery";
import CommanderName from "../components/CommanderName";
import CommanderColors from "../components/CommanderColors";
import CumulativeTagFilter from "../components/CumulativeTagFilter";
import SimpleTable from "../components/SimpleTable";
import { loadCommanderIndex } from "../lib/api";
import { cumulativeZColumn } from "../lib/cumulativeTagUtils";
import { formatDecimal, formatNumber } from "../lib/formatters";
import { sortRows, toggleSortDirection } from "../lib/tableUtils";
import { useCumulativeTags } from "../lib/useCumulativeTags";

export default function CommanderSearchPage() {
  const cumulative = useCumulativeTags();
  const { filterRows: filterCumulativeRows, active: cumulativeActive } = cumulative;
  const [sort, setSort] = useState({ key: "cumulative_z", direction: "desc" });
  const [commanders, setCommanders] = useState([]);
  const [query, setQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(100);
  const [state, setState] = useState({ loading: true, error: null });

  useEffect(() => {
    async function loadData() {
      try {
        const data = await loadCommanderIndex();

        const sortedByPopularity = (Array.isArray(data) ? data : []).sort(
          (a, b) => Number(b.total_decks || 0) - Number(a.total_decks || 0)
        );

        setCommanders(sortedByPopularity);
        setState({ loading: false, error: null });
      } catch (error) {
        setState({ loading: false, error: error.message });
      }
    }

    loadData();
  }, []);

  const filteredCommanders = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    const matching = filterCumulativeRows(commanders).filter((commander) =>
      String(commander.commander_name || "")
        .toLowerCase()
        .includes(normalizedQuery)
    );
    return cumulativeActive ? sortRows(matching, sort.key, sort.direction) : matching;
  }, [commanders, query, filterCumulativeRows, cumulativeActive, sort]);

  const visibleCommanders = filteredCommanders.slice(0, visibleCount);
  const columns = [
    { key: "commander_name", header: "Commander", sortable: true,
      render: (row) => <CommanderName commander={row} className="table-commander-link" /> },
    cumulativeZColumn,
    { key: "cumulative_tag_scores", header: "Selected tag z-scores",
      render: (row) => row.cumulative_tag_scores.map((tag) => `${tag.tag_name}: ${formatDecimal(tag.z)}`).join(" + ") },
    { key: "color_identity", header: "Colors", render: (row) => <CommanderColors colorIdentity={row.color_identity} /> },
    { key: "total_decks", header: "Total Decks", sortable: true, render: (row) => formatNumber(row.total_decks) },
  ];

  if (state.loading) {
    return <p className="muted">Loading commander index…</p>;
  }

  if (state.error) {
    return <p className="error-message">Could not load commanders: {state.error}</p>;
  }

  return (
    <section className="page">
      <div className="page-header">
        <h1>Commander Search</h1>
        <p>
          Commanders are shown by popularity by default. Search all commanders in
          the exported dataset and click a commander to load its complete tag table.
          Combine tags to rank commanders by their cumulative z-score.
        </p>
      </div>

      <CumulativeTagFilter filter={cumulative} onChange={(value) => {
        cumulative.setQuery(value);
        setSort({ key: "cumulative_z", direction: "desc" });
        setVisibleCount(100);
      }} />

      <div className="search-panel">
        <label htmlFor="commander-search">Search commander name</label>
        <input
          id="commander-search"
          type="search"
          placeholder="Search commander name…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setVisibleCount(100);
          }}
        />
      </div>

      <p className="muted">
        Showing {formatNumber(visibleCommanders.length)} of{" "}
        {formatNumber(filteredCommanders.length)} matching commanders.
      </p>

      {cumulativeActive ? (
        <SimpleTable columns={columns} rows={visibleCommanders}
          emptyMessage={cumulative.loading ? "Loading tag scores…" : "No commanders match every selected tag and the current search."}
          sortKey={sort.key} sortDirection={sort.direction}
          onSort={(key) => setSort((current) => ({ key, direction: toggleSortDirection(current.key, key, current.direction) }))} />
      ) : <div className="commander-grid">
        {visibleCommanders.map((commander) => (
          <article className="commander-card" key={commander.commander_slug}>
            <div className="commander-card__image-wrap">
              <CommanderImageGallery
                commander={commander}
                variant="card"
              />
            </div>

            <div className="commander-card__content">
              <div className="commander-card__header">
                <div>
                  <h2>
                    <CommanderName
                      commander={commander}
                      className="commander-card__title-link"
                    />
                  </h2>
                  <p><CommanderColors colorIdentity={commander.color_identity} /></p>
                </div>

                <span className="pill">
                  {formatNumber(commander.total_decks)} decks
                </span>
              </div>

              <Link
                className="commander-card__link"
                to={`/commanders/${commander.commander_slug}`}
              >
                View complete tag table →
              </Link>
            </div>
          </article>
        ))}
      </div>}

      {visibleCount < filteredCommanders.length ? (
        <div className="load-more-wrap">
          <button
            className="button"
            type="button"
            onClick={() => setVisibleCount((current) => current + 100)}
          >
            Load more commanders
          </button>
        </div>
      ) : null}
    </section>
  );
}
