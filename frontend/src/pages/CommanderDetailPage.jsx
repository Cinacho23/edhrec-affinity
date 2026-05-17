import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import CommanderImageGallery from "../components/CommanderImageGallery";
import SimpleTable from "../components/SimpleTable";
import { loadCommanderDetail } from "../lib/api";
import {
  formatColorIdentity,
  formatDecimal,
  formatNumber,
  formatPercent,
  formatRank,
} from "../lib/formatters";

export default function CommanderDetailPage() {
  const { commanderSlug } = useParams();

  const [rows, setRows] = useState([]);
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

  const strongestTag = useMemo(() => {
    return [...rows]
      .filter((row) => row.z !== null && row.z !== undefined)
      .sort((a, b) => Number(b.z) - Number(a.z))[0];
  }, [rows]);

  const largestTag = useMemo(() => {
    return [...rows]
      .filter((row) => row.tag_decks !== null && row.tag_decks !== undefined)
      .sort((a, b) => Number(b.tag_decks) - Number(a.tag_decks))[0];
  }, [rows]);

  const columns = [
    { key: "tag_name", header: "Tag" },
    {
      key: "tag_decks",
      header: "Tag Decks",
      render: (row) => formatNumber(row.tag_decks),
    },
    {
      key: "tag_affinity_pct",
      header: "Affinity",
      render: (row) => formatPercent(row.tag_affinity_pct),
    },
    {
      key: "z",
      header: "Z-Score",
      render: (row) => formatDecimal(row.z),
    },
    {
      key: "rank_within_tag_by_z",
      header: "Rank",
      render: (row) => formatRank(row.rank_within_tag_by_z),
    },
    {
      key: "percentile_within_tag",
      header: "Percentile",
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
        <Link className="detail-back-link" to="/commanders">
          ← Back to commander search
        </Link>
      </section>
    );
  }

  if (!commander) {
    return (
      <section className="page">
        <p className="error-message">Commander not found.</p>
        <Link className="detail-back-link" to="/commanders">
          ← Back to commander search
        </Link>
      </section>
    );
  }

  return (
    <section className="page">
      <div className="detail-back-link-wrap">
        <Link className="detail-back-link" to="/commanders">
          ← Back to commander search
        </Link>
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
            <span className="pill">{formatNumber(rows.length)} tags</span>
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
          <p className="table-count">{formatNumber(rows.length)} rows</p>
        </div>

        <SimpleTable columns={columns} rows={rows} />
      </section>
    </section>
  );
}