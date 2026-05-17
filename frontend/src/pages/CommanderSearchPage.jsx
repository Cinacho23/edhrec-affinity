import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import CommanderImageGallery from "../components/CommanderImageGallery";
import { loadCommanderIndex } from "../lib/api";
import { formatColorIdentity, formatNumber } from "../lib/formatters";

export default function CommanderSearchPage() {
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

    if (!normalizedQuery) {
      return commanders;
    }

    return commanders.filter((commander) =>
      String(commander.commander_name || "")
        .toLowerCase()
        .includes(normalizedQuery)
    );
  }, [commanders, query]);

  const visibleCommanders = filteredCommanders.slice(0, visibleCount);

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
        </p>
      </div>

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

      <div className="commander-grid">
        {visibleCommanders.map((commander) => (
          <article className="commander-card" key={commander.commander_slug}>
            <div className="commander-card__image-wrap">
              <CommanderImageGallery
                commanderName={commander.commander_name}
                cardImageUrl={commander.card_image_url}
                partnerCardImageUrls={commander.partner_card_image_urls}
                scryfallUri={commander.scryfall_uri}
                partnerScryfallUris={commander.partner_scryfall_uris}
                variant="card"
              />
            </div>

            <div className="commander-card__content">
              <div className="commander-card__header">
                <div>
                  <h2>
                    <Link
                      className="commander-card__title-link"
                      to={`/commanders/${commander.commander_slug}`}
                    >
                      {commander.commander_name}
                    </Link>
                  </h2>
                  <p>{formatColorIdentity(commander.color_identity)}</p>
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
      </div>

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