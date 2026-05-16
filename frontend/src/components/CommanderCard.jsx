import { Link } from "react-router";

import CommanderImageGallery from "./CommanderImageGallery.jsx";

import {
  formatNumber,
  formatPercent,
  formatSignedNumber,
  formatZScore,
} from "../lib/formatters.js";

/*
  CommanderCard displays one commander in the search prototype.

  New behavior:
  - Single commanders display one card image.
  - Partner / paired commanders display two card images when
    partner_card_image_urls contains two URLs.
  - The title and image link to:
      /commanders/:commanderSlug
*/

const COLOR_LABELS = {
  W: "White",
  U: "Blue",
  B: "Black",
  R: "Red",
  G: "Green",
};

function normalizeColorArray(colorIdentity) {
  if (Array.isArray(colorIdentity)) {
    return colorIdentity;
  }

  if (typeof colorIdentity === "string") {
    const trimmed = colorIdentity.trim();

    if (!trimmed) {
      return [];
    }

    if (trimmed.includes(",")) {
      return trimmed
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean);
    }

    if (/^[WUBRG]+$/.test(trimmed)) {
      return trimmed.split("");
    }

    return [trimmed];
  }

  return [];
}

function getColorDisplay(colorIdentity) {
  const colors = normalizeColorArray(colorIdentity);

  if (colors.length === 0) {
    return "Colorless / unknown";
  }

  return colors.join("");
}

function getColorTitle(colorIdentity) {
  const colors = normalizeColorArray(colorIdentity);

  if (colors.length === 0) {
    return "Colorless or unknown color identity";
  }

  return colors.map((color) => COLOR_LABELS[color] ?? color).join(", ");
}

export default function CommanderCard({ commander }) {
  const colorDisplay = getColorDisplay(commander.color_identity);
  const colorTitle = getColorTitle(commander.color_identity);
  const detailPath = `/commanders/${commander.commander_slug}`;

  return (
    <article className="commander-card">
      <div className="commander-card__image-wrap">
        <CommanderImageGallery
          commander={commander}
          size="card"
          linked
          linkTo={detailPath}
        />
      </div>

      <div className="commander-card__content">
        <div className="commander-card__header">
          <div>
            <h2>
              <Link className="commander-card__title-link" to={detailPath}>
                {commander.commander_name}
              </Link>
            </h2>

            <p className="muted">
              {formatNumber(commander.total_decks)} total decks
            </p>

            {commander.scryfall_uri ? (
              <a
                className="commander-card__link"
                href={commander.scryfall_uri}
                target="_blank"
                rel="noreferrer"
              >
                View on Scryfall
              </a>
            ) : null}
          </div>

          <span className="pill" title={colorTitle}>
            {colorDisplay}
          </span>
        </div>

        <section className="tag-preview" aria-label="Top tag affinities">
          <h3>Top tag affinities</h3>

          {commander.top_tags.length === 0 ? (
            <p className="muted">No tag rows available.</p>
          ) : (
            <div className="tag-list">
              {commander.top_tags.map((tag) => (
                <div
                  className="tag-row"
                  key={`${commander.commander_slug}-${tag.tag_slug}`}
                >
                  <div>
                    <strong>{tag.tag_name}</strong>

                    <span className="muted">
                      {formatNumber(tag.tag_decks)} tag decks ·{" "}
                      {formatPercent(tag.tag_affinity_pct)}
                    </span>
                  </div>

                  <div className="tag-row__metrics">
                    <span>Z {formatZScore(tag.z)}</span>

                    {tag.rank_within_tag_by_z ? (
                      <span>
                        Rank #{formatNumber(tag.rank_within_tag_by_z)}
                      </span>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="trend-preview" aria-label="Trend preview">
          <h3>Trend status</h3>

          {commander.trend_status === "no_previous_snapshot" ? (
            <p className="muted">
              Trend data starts after the second processed snapshot.
            </p>
          ) : commander.best_rank_delta == null ? (
            <p className="muted">No rank movement available yet.</p>
          ) : (
            <p>
              Best rank movement:{" "}
              <strong>{formatSignedNumber(commander.best_rank_delta)}</strong>
            </p>
          )}
        </section>

        <Link className="button commander-card__details-button" to={detailPath}>
          View full tag details
        </Link>
      </div>
    </article>
  );
}