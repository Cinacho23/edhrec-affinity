/*
  TrendBadge.jsx

  Displays trend information in a safe way.

  Important project rule from Chat 8:
  - no_previous_snapshot does NOT mean "no change".
  - It means there is no earlier snapshot to compare against yet.

  Once a second scrape exists, rank_delta and other delta fields will start
  becoming useful.
*/

import { getTrendStatus, getNumber } from "../lib/filterUtils";

function formatRankDelta(delta) {
  if (delta === null || delta === undefined || Number.isNaN(Number(delta))) {
    return "";
  }

  const numericDelta = Number(delta);

  if (numericDelta > 0) {
    return `Rank +${numericDelta}`;
  }

  if (numericDelta < 0) {
    return `Rank ${numericDelta}`;
  }

  return "No rank change";
}

export default function TrendBadge({ row, compact = false }) {
  const status = getTrendStatus(row);
  const rankDelta = getNumber(row.rank_delta);

  if (status === "no_previous_snapshot") {
    return (
      <span className="trend-badge trend-neutral">
        {compact ? "No history" : "No previous snapshot yet"}
      </span>
    );
  }

  if (status === "new_pair") {
    return <span className="trend-badge trend-positive">New pair</span>;
  }

  if (status === "removed_pair") {
    return <span className="trend-badge trend-negative">Removed pair</span>;
  }

  if (rankDelta !== null && rankDelta > 0) {
    return (
      <span className="trend-badge trend-positive">
        ▲ {formatRankDelta(rankDelta)}
      </span>
    );
  }

  if (rankDelta !== null && rankDelta < 0) {
    return (
      <span className="trend-badge trend-negative">
        ▼ {formatRankDelta(rankDelta)}
      </span>
    );
  }

  if (status === "existing") {
    return <span className="trend-badge trend-neutral">Existing</span>;
  }

  return <span className="trend-badge trend-neutral">Trend unavailable</span>;
}