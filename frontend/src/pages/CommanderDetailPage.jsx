/*
  CommanderDetailPage.jsx

  This page shows the complete tag list for one commander.

  Route:
    /commanders/:commanderSlug

  Example:
    /commanders/jasmine-boreal-of-the-seven

  New behavior:
  - Single commanders display one card image.
  - Partner / paired commanders display two card images when available.
*/

import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import CommanderImageGallery from "../components/CommanderImageGallery.jsx";
import DataTable from "../components/DataTable.jsx";
import ErrorState from "../components/ErrorState.jsx";
import LoadingState from "../components/LoadingState.jsx";
import TrendBadge from "../components/TrendBadge.jsx";

import { loadAffinityRowsWithTrends } from "../lib/api.js";
import {
  getCommanderBySlug,
  getNumeric,
  normalizeAffinityRow,
} from "../lib/commanderUtils.js";

import {
  formatNumber,
  formatPercent,
  formatSignedNumber,
  formatZScore,
} from "../lib/formatters.js";

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

function formatPercentile(value) {
  const numeric = getNumeric(value, null);

  if (numeric === null) {
    return "—";
  }

  return `${(numeric * 100).toFixed(1)}%`;
}

function createCommanderTagColumns() {
  return [
    {
      id: "tag_name",
      header: "Tag",
      accessorFn: (row) => row.tag_name ?? "Unknown tag",
      cell: (info) => info.getValue(),
    },
    {
      id: "tag_decks",
      header: "Tag Decks",
      accessorFn: (row) => getNumeric(row.tag_decks, null),
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "affinity_pct",
      header: "Affinity %",
      accessorFn: (row) => getNumeric(row.tag_affinity_pct, null),
      cell: (info) => formatPercent(info.getValue()),
    },
    {
      id: "z",
      header: "Z-Score",
      accessorFn: (row) => getNumeric(row.z, null),
      cell: (info) => formatZScore(info.getValue()),
    },
    {
      id: "rank_within_tag_by_z",
      header: "Rank by Z",
      accessorFn: (row) => getNumeric(row.rank_within_tag_by_z, null),
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "rank_within_tag_by_pct",
      header: "Rank by %",
      accessorFn: (row) => getNumeric(row.rank_within_tag_by_pct, null),
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "rank_within_tag_by_tag_decks",
      header: "Rank by Tag Decks",
      accessorFn: (row) => getNumeric(row.rank_within_tag_by_tag_decks, null),
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "percentile_within_tag",
      header: "Percentile",
      accessorFn: (row) => getNumeric(row.percentile_within_tag, null),
      cell: (info) => formatPercentile(info.getValue()),
    },
    {
      id: "tag_mean_pct",
      header: "Tag Mean",
      accessorFn: (row) => getNumeric(row.tag_mean_pct, null),
      cell: (info) => formatPercent(info.getValue()),
    },
    {
      id: "tag_std_pct",
      header: "Tag Std. Dev.",
      accessorFn: (row) => getNumeric(row.tag_std_pct, null),
      cell: (info) => formatPercent(info.getValue()),
    },
    {
      id: "rank_delta",
      header: "Rank Δ",
      accessorFn: (row) => getNumeric(row.rank_delta, null),
      cell: (info) => formatSignedNumber(info.getValue()),
    },
    {
      id: "trend",
      header: "Trend",
      accessorFn: (row) => row.trend_status ?? "",
      enableSorting: false,
      cell: (info) => <TrendBadge row={info.row.original} compact />,
    },
  ];
}

export default function CommanderDetailPage() {
  const { commanderSlug } = useParams();

  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [tagQuery, setTagQuery] = useState("");

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

  const commander = useMemo(() => {
    if (!commanderSlug) {
      return null;
    }

    return getCommanderBySlug(rows, commanderSlug);
  }, [rows, commanderSlug]);

  const normalizedCommanderRows = useMemo(() => {
    if (!commanderSlug) {
      return [];
    }

    return rows
      .map(normalizeAffinityRow)
      .filter((row) => row.commander_slug === commanderSlug)
      .sort((a, b) => {
        const zA = getNumeric(a.z, Number.NEGATIVE_INFINITY);
        const zB = getNumeric(b.z, Number.NEGATIVE_INFINITY);

        if (zB !== zA) {
          return zB - zA;
        }

        return getNumeric(b.tag_decks, 0) - getNumeric(a.tag_decks, 0);
      });
  }, [rows, commanderSlug]);

  const filteredTagRows = useMemo(() => {
    const normalizedQuery = tagQuery.trim().toLowerCase();

    if (!normalizedQuery) {
      return normalizedCommanderRows;
    }

    return normalizedCommanderRows.filter((row) => {
      return (
        String(row.tag_name ?? "")
          .toLowerCase()
          .includes(normalizedQuery) ||
        String(row.tag_slug ?? "")
          .toLowerCase()
          .includes(normalizedQuery)
      );
    });
  }, [normalizedCommanderRows, tagQuery]);

  const columns = useMemo(() => createCommanderTagColumns(), []);

  const strongestTag = normalizedCommanderRows[0] ?? null;

  const highestTagDeckRow = useMemo(() => {
    return [...normalizedCommanderRows].sort((a, b) => {
      return getNumeric(b.tag_decks, 0) - getNumeric(a.tag_decks, 0);
    })[0];
  }, [normalizedCommanderRows]);

  if (loading) {
    return <LoadingState message="Loading commander details..." />;
  }

  if (error) {
    return <ErrorState title="Could not load commander details" error={error} />;
  }

  if (!commander) {
    return (
      <div className="page">
        <section className="status-box">
          <p className="eyebrow">Commander not found</p>
          <h1>No data for this commander</h1>

          <p className="muted">
            The URL slug <code>{commanderSlug}</code> did not match any
            commander in <code>affinity_rows_with_trends.json</code>.
          </p>

          <Link className="button" to="/commanders">
            Back to commander search
          </Link>
        </section>
      </div>
    );
  }

  const colorDisplay = getColorDisplay(commander.color_identity);
  const colorTitle = getColorTitle(commander.color_identity);

  return (
    <div className="page">
      <div className="detail-back-link-wrap">
        <Link className="detail-back-link" to="/commanders">
          ← Back to commander search
        </Link>
      </div>

      <section className="commander-detail-hero">
        <div className="commander-detail-hero__image-wrap">
          <CommanderImageGallery commander={commander} size="detail" />
        </div>

        <div className="commander-detail-hero__content">
          <p className="eyebrow">Commander Detail</p>
          <h1>{commander.commander_name}</h1>

          <p className="hero__text">
            Complete tag profile for this commander. The table below shows every
            analyzed tag row for this commander, including tag deck count,
            affinity percentage, z-score, percentile, rank, and trend fields.
          </p>

          <div className="commander-detail-meta">
            <span className="pill" title={colorTitle}>
              {colorDisplay}
            </span>

            <span className="pill">
              {formatNumber(commander.total_decks)} total decks
            </span>

            <span className="pill">
              {formatNumber(normalizedCommanderRows.length)} tags
            </span>
          </div>

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
      </section>

      <section className="stat-row">
        <article className="stat-card">
          <span>Strongest tag by z-score</span>
          <strong>{strongestTag?.tag_name ?? "—"}</strong>
          <p className="stat-card__helper">
            Z {formatZScore(strongestTag?.z)}
          </p>
        </article>

        <article className="stat-card">
          <span>Largest tag by decks</span>
          <strong>{highestTagDeckRow?.tag_name ?? "—"}</strong>
          <p className="stat-card__helper">
            {formatNumber(highestTagDeckRow?.tag_decks)} tag decks
          </p>
        </article>

        <article className="stat-card">
          <span>Best percentile</span>
          <strong>{formatPercentile(strongestTag?.percentile_within_tag)}</strong>
          <p className="stat-card__helper">
            Based on the current strongest z-score row.
          </p>
        </article>

        <article className="stat-card">
          <span>Trend status</span>
          <strong>
            {commander.trend_status === "no_previous_snapshot"
              ? "No history yet"
              : commander.trend_status ?? "Unavailable"}
          </strong>
          <p className="stat-card__helper">
            Real trend deltas begin after a second snapshot.
          </p>
        </article>
      </section>

      <section className="search-panel">
        <label htmlFor="commander-tag-search">Search this commander’s tags</label>

        <input
          id="commander-tag-search"
          type="search"
          value={tagQuery}
          onChange={(event) => setTagQuery(event.target.value)}
          placeholder="Try Tokens, Aggro, cEDH..."
        />

        <p className="muted">
          Showing {formatNumber(filteredTagRows.length)} of{" "}
          {formatNumber(normalizedCommanderRows.length)} tag rows for this
          commander.
        </p>
      </section>

      <DataTable
        data={filteredTagRows}
        columns={columns}
        tableLabel={`Complete tag table for ${commander.commander_name}`}
        initialSorting={[{ id: "z", desc: true }]}
        pageSize={25}
      />
    </div>
  );
}