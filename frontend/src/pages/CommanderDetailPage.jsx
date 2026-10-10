import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import CommanderImageGallery from "../components/CommanderImageGallery";
import SimpleTable from "../components/SimpleTable";
import { loadCommanderDetail } from "../lib/api";
import {
  formatAdjustedAffinity,
  formatAffinityInterval,
  formatAffinityProbability,
  formatBuildRarity,
} from "../lib/affinityDisplay";
import {
  getVisibleCommanderTagRows,
  MIN_COMMANDER_TAG_DECKS,
} from "../lib/commanderUtils";
import {
  formatColorIdentity,
  formatDecimal,
  formatNumber,
  formatPercent,
  formatRank,
} from "../lib/formatters";
import { passesMin, sortRows, toggleSortDirection } from "../lib/tableUtils";

export default function CommanderDetailPage() {
  const { commanderSlug } = useParams();
  const navigate = useNavigate();

  const [rows, setRows] = useState([]);
  const [minBuildRarity, setMinBuildRarity] = useState("");
  const [sort, setSort] = useState({ key: "z", direction: "desc" });
  const [state, setState] = useState({ loading: true, error: null });

  useEffect(() => {
    async function loadData() {
      try {
        const data = await loadCommanderDetail(commanderSlug);
        setRows(Array.isArray(data) ? data : []);
        setState({ loading: false, error: null });
      } catch (error) {
        setState({ loading: false, error: error.message });
      }
    }

    loadData();
  }, [commanderSlug]);

  const commander = rows[0];
  const tagRows = useMemo(() => getVisibleCommanderTagRows(rows), [rows]);

  const visibleRows = useMemo(() => {
    const filtered = tagRows.filter((row) => passesMin(row, "build_rarity", minBuildRarity));
    return sortRows(filtered, sort.key, sort.direction);
  }, [tagRows, minBuildRarity, sort]);

  function handleSort(key) {
    setSort((current) => ({
      key,
      direction: toggleSortDirection(current.key, key, current.direction),
    }));
  }

  function returnToPreviousPage() {
    navigate(-1);
  }

  const strongestTag = useMemo(() => {
    return [...tagRows]
      .filter((row) => row.z !== null && row.z !== undefined)
      .sort((a, b) => Number(b.z) - Number(a.z))[0];
  }, [tagRows]);

  const largestTag = useMemo(() => {
    return [...tagRows]
      .filter((row) => row.tag_decks !== null && row.tag_decks !== undefined)
      .sort((a, b) => Number(b.tag_decks) - Number(a.tag_decks))[0];
  }, [tagRows]);

  const columns = [
    { key: "tag_name", header: "Tag", sortable: true },
    {
      key: "tag_decks",
      header: "Tag Decks",
      sortable: true,
      render: (row) => formatNumber(row.tag_decks),
    },
    {
      key: "tag_affinity_pct",
      header: "Raw Affinity",
      sortable: true,
      render: (row) => formatAffinityProbability(row.tag_affinity_pct),
    },
    {
      key: "tag_affinity_adjusted_pct",
      header: "Adjusted Affinity",
      sortable: true,
      render: formatAdjustedAffinity,
    },
    {
      key: "build_rarity",
      header: "Build Rarity",
      sortable: true,
      render: formatBuildRarity,
    },
    {
      key: "affinity_interval",
      header: "95% Range",
      render: formatAffinityInterval,
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
      key: "percentile_within_tag",
      header: "Percentile",
      sortable: true,
      render: (row) => formatPercent(row.percentile_within_tag),
    },
  ];

  if (state.loading) {
    return <p className="muted">Loading commander detail…</p>;
  }

  if (state.error) {
    return (
      <section className="page">
        <p className="error-message">Could not load commander: {state.error}</p>
        <button
          className="detail-back-link"
          type="button"
          onClick={returnToPreviousPage}
        >
          ← Return to previous page
        </button>
      </section>
    );
  }

  if (!commander) {
    return (
      <section className="page">
        <p className="error-message">Commander not found.</p>
        <button
          className="detail-back-link"
          type="button"
          onClick={returnToPreviousPage}
        >
          ← Return to previous page
        </button>
      </section>
    );
  }

  return (
    <section className="page">
      <div className="detail-back-link-wrap">
        <button
          className="detail-back-link"
          type="button"
          onClick={returnToPreviousPage}
        >
          ← Return to previous page
        </button>
      </div>

      <section className="commander-detail-hero">
        <div className="commander-detail-hero__image-wrap">
          <CommanderImageGallery
            commanderName={commander.commander_name}
            cardImageUrl={commander.card_image_url}
            partnerCardImageUrls={commander.partner_card_image_urls}
            scryfallUri={commander.scryfall_uri}
            partnerScryfallUris={commander.partner_scryfall_uris}
            variant="detail"
          />
        </div>

        <div className="commander-detail-hero__content">
          <h1>{commander.commander_name}</h1>

          <div className="commander-detail-meta">
            <span className="pill">{formatColorIdentity(commander.color_identity)}</span>
            <span className="pill">{formatNumber(commander.total_decks)} decks</span>
            <span className="pill">{formatNumber(tagRows.length)} tags</span>
          </div>

          {strongestTag ? (
            <p>
              Strongest tag by z-score: <strong>{strongestTag.tag_name}</strong>{" "}
              ({formatDecimal(strongestTag.z)})
            </p>
          ) : null}

          {largestTag ? (
            <p>
              Largest tag by deck count: <strong>{largestTag.tag_name}</strong>{" "}
              ({formatNumber(largestTag.tag_decks)} decks)
            </p>
          ) : null}
        </div>
      </section>

      <section className="data-table-section">
        <div className="table-toolbar">
          <div>
            <p className="eyebrow">Commander tags</p>
            <h2>Complete tag table</h2>
          </div>
          <p className="table-count">{formatNumber(visibleRows.length)} of {formatNumber(tagRows.length)} rows</p>
        </div>

        <p className="muted table-note">
          Tags appear with at least {MIN_COMMANDER_TAG_DECKS} decks for this commander.
          {" "}Raw affinity is the observed share of tagged decks. Adjusted affinity
          accounts for sample size; its 95% range shows uncertainty under the
          model. Build Rarity shows how uncommon a tag is within this commander:
          ≈1 in 50 means an estimated 2% share. Higher values mean rarer builds.
          Sort Build Rarity to explore uncommon themes, then check whether the
          commander’s abilities support the idea. “Unadjusted” rows retain the
          previous score, and missing
          estimates or ranges appear as —. <Link to="/methodology">How it works</Link>
        </p>

        <div className="filter-grid">
          <label>
            <span>Minimum build rarity (1 in N)</span>
            <input
              type="number"
              min="1"
              step="any"
              value={minBuildRarity}
              onChange={(event) => setMinBuildRarity(event.target.value)}
              placeholder="e.g. 50"
            />
          </label>
        </div>
        <SimpleTable
          columns={columns}
          rows={visibleRows}
          sortKey={sort.key}
          sortDirection={sort.direction}
          onSort={handleSort}
          emptyMessage={tagRows.length === 0
            ? `No tags have at least ${MIN_COMMANDER_TAG_DECKS} decks for this commander.`
            : "No reported tags match this rarity filter."}
        />
      </section>
    </section>
  );
}
