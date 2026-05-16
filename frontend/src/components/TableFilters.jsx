/*
  TableFilters.jsx

  Shared filter controls for the leaderboard and tag explorer pages.

  These filters are "page-level" filters. That means:
  - The page filters the raw JSON rows first.
  - Then DataTable receives only the filtered rows.
  - DataTable still handles sorting, table search, and pagination.

  This separation is beginner-friendly because the filtering rules live in
  filterUtils.js instead of being spread throughout TanStack Table internals.
*/

export default function TableFilters({
  filters,
  setFilters,
  mode = "leaderboard",
  tagOptions = [],
}) {
  function updateFilter(name, value) {
    setFilters((currentFilters) => ({
      ...currentFilters,
      [name]: value,
    }));
  }

  function resetFilters() {
    if (mode === "leaderboard") {
      setFilters({
        commanderText: "",
        tagText: "",
        colorIdentity: "",
        minTotalDecks: "200",
        minTagDecks: "5",
        minZ: "",
        minAffinityPct: "",
        trendStatus: "",
      });
      return;
    }

    setFilters({
      commanderText: "",
      colorIdentity: "",
      minTotalDecks: "200",
      minTagDecks: "5",
      minZ: "",
      minAffinityPct: "",
      trendStatus: "",
    });
  }

  return (
    <section className="filter-panel" aria-label="Table filters">
      <div className="filter-panel-header">
        <div>
          <p className="eyebrow">Filters</p>
          <h2>Refine results</h2>
        </div>

        <button type="button" className="secondary-button" onClick={resetFilters}>
          Reset filters
        </button>
      </div>

      <div className="filter-grid">
        <label>
          <span>Commander</span>
          <input
            type="search"
            value={filters.commanderText}
            onChange={(event) => updateFilter("commanderText", event.target.value)}
            placeholder="Jasmine, Tenth Doctor, Krenko..."
          />
        </label>

        {mode === "leaderboard" && (
          <label>
            <span>Tag</span>
            <input
              type="search"
              value={filters.tagText}
              onChange={(event) => updateFilter("tagText", event.target.value)}
              placeholder="Tokens, Aggro, cEDH..."
              list="leaderboard-tag-options"
            />

            <datalist id="leaderboard-tag-options">
              {tagOptions.map((tag) => (
                <option key={tag.slug} value={tag.name} />
              ))}
            </datalist>
          </label>
        )}

        <label>
          <span>Color identity</span>
          <input
            type="search"
            value={filters.colorIdentity}
            onChange={(event) => updateFilter("colorIdentity", event.target.value)}
            placeholder="W, U, B, R, G, Azorius..."
          />
        </label>

        <label>
          <span>Minimum total decks</span>
          <input
            type="number"
            min="0"
            value={filters.minTotalDecks}
            onChange={(event) => updateFilter("minTotalDecks", event.target.value)}
          />
        </label>

        <label>
          <span>Minimum tag decks</span>
          <input
            type="number"
            min="0"
            value={filters.minTagDecks}
            onChange={(event) => updateFilter("minTagDecks", event.target.value)}
          />
        </label>

        <label>
          <span>Minimum z-score</span>
          <input
            type="number"
            step="0.1"
            value={filters.minZ}
            onChange={(event) => updateFilter("minZ", event.target.value)}
            placeholder="Example: 2"
          />
        </label>

        <label>
          <span>Minimum affinity %</span>
          <input
            type="number"
            min="0"
            step="0.1"
            value={filters.minAffinityPct}
            onChange={(event) => updateFilter("minAffinityPct", event.target.value)}
            placeholder="Example: 10"
          />
        </label>

        <label>
          <span>Trend status</span>
          <select
            value={filters.trendStatus}
            onChange={(event) => updateFilter("trendStatus", event.target.value)}
          >
            <option value="">Any trend status</option>
            <option value="no_previous_snapshot">No history yet</option>
            <option value="existing">Existing pair</option>
            <option value="new_pair">New pair</option>
            <option value="removed_pair">Removed pair</option>
          </select>
        </label>
      </div>
    </section>
  );
}