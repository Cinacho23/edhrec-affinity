/*
  tableColumns.js

  TanStack Table column definitions for:
  - Global Leaderboard
  - Tag Explorer

  This file makes commander names clickable in both tables.

  When a user clicks a commander name, they go to:

    /commanders/:commanderSlug

  Example:

    /commanders/jasmine-boreal-of-the-seven

  The commander detail route should already exist in App.jsx:

    <Route
      path="/commanders/:commanderSlug"
      element={<CommanderDetailPage />}
    />

  This file also reads both old and new backend field names because the trend
  pipeline introduced suffixed/current fields such as:

    total_decks_current
    total_decks_x
    tag_decks_current
    tag_decks_x
    z_current
    z_x
    rank_within_tag_by_z_current
    rank_within_tag_by_z_x
*/

import React from "react";

import CommanderName from "../components/CommanderName";
import CommanderColors from "../components/CommanderColors";
import TrendBadge from "../components/TrendBadge";
import { getColorIdentityRank, normalizeColorIdentity } from "./colorIdentity.js";

import {
  formatDecimal,
  formatNumber,
  formatPercentFromDisplayValue,
  formatZScore,
  getAffinityDisplayPct,
  getColorIdentity,
  getNumber,
} from "./filterUtils";

function getSortableColorIdentity(row) {
  const identity = normalizeColorIdentity(getColorIdentity(row));
  return identity === null ? undefined : identity || "C";
}

function compareColorIdentities(rowA, rowB, columnId) {
  return (
    getColorIdentityRank(rowA.getValue(columnId)) -
    getColorIdentityRank(rowB.getValue(columnId))
  );
}

/*
  firstNonEmpty() chooses the first usable value from a list.

  This matters because your data files now contain multiple versions of the
  same logical field after the trend merge.

  Example:
    z
    z_x
    z_current
*/
function firstNonEmpty(...values) {
  for (const value of values) {
    if (value !== null && value !== undefined && value !== "") {
      return value;
    }
  }

  return null;
}

function getCommanderName(row) {
  return firstNonEmpty(row.commander_name, row.commander, "Unknown commander");
}

function getTagName(row) {
  return firstNonEmpty(row.tag_name, row.tag, "Unknown tag");
}

function getTotalDecks(row) {
  return getNumber(
    firstNonEmpty(
      row.total_decks_current,
      row.total_decks,
      row.total_decks_x,
      row.total_decks_y
    )
  );
}

function getTagDecks(row) {
  return getNumber(
    firstNonEmpty(
      row.tag_decks_current,
      row.tag_decks,
      row.tag_decks_x,
      row.tag_decks_y
    )
  );
}

function getZScore(row) {
  return getNumber(firstNonEmpty(row.z_current, row.z, row.z_x, row.z_y));
}

function getRankByZ(row) {
  return getNumber(
    firstNonEmpty(
      row.rank_within_tag_by_z_current,
      row.rank_within_tag_by_z,
      row.rank_within_tag_by_z_x,
      row.rank_within_tag_by_z_y,
      row.rank_within_tag,
      row.rank
    )
  );
}

function getRankByPct(row) {
  return getNumber(firstNonEmpty(row.rank_within_tag_by_pct, row.rank_by_pct));
}

function getRankByTagDecks(row) {
  return getNumber(
    firstNonEmpty(row.rank_within_tag_by_tag_decks, row.rank_by_tag_decks)
  );
}

function getTagMeanPct(row) {
  return getNumber(firstNonEmpty(row.tag_mean_pct, row.mean_pct));
}

function getTagStdPct(row) {
  return getNumber(firstNonEmpty(row.tag_std_pct, row.std_pct));
}

function getRankDelta(row) {
  return getNumber(
    firstNonEmpty(row.rank_delta, row.rank_within_tag_by_z_delta)
  );
}

function getTrendSortValue(row) {
  return firstNonEmpty(row.rank_delta, row.snapshot_status, row.trend_status, "");
}

function getPercentileDisplay(row) {
  const percentile = getNumber(row.percentile_within_tag);

  if (percentile === null) {
    return "—";
  }

  return `${(percentile * 100).toFixed(1)}%`;
}

function formatPercentFromDecimal(value) {
  const numericValue = getNumber(value);

  if (numericValue === null) {
    return "—";
  }

  return `${(numericValue * 100).toFixed(2)}%`;
}

export function createLeaderboardColumns() {
  return [
    {
      id: "commander_name",
      header: "Commander",
      accessorFn: getCommanderName,

      /*
        This is the important part:
        the displayed commander name becomes a React Router Link.
      */
      cell: (info) =>
        React.createElement(CommanderName, {
          commander: info.row.original,
          className: "table-commander-link",
        }),
    },
    {
      id: "tag_name",
      header: "Tag",
      accessorFn: getTagName,
      cell: (info) => info.getValue(),
    },
    {
      id: "color_identity",
      header: "Colors",
      accessorFn: getSortableColorIdentity,
      sortingFn: compareColorIdentities,
      sortUndefined: "last",
      sortDescFirst: false,
      cell: (info) => React.createElement(CommanderColors, { colorIdentity: info.getValue() }),
    },
    {
      id: "total_decks",
      header: "Total Decks",
      accessorFn: getTotalDecks,
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "tag_decks",
      header: "Tag Decks",
      accessorFn: getTagDecks,
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "affinity_pct",
      header: "Affinity %",
      accessorFn: getAffinityDisplayPct,
      cell: (info) => formatPercentFromDisplayValue(info.getValue()),
    },
    {
      id: "z",
      header: "Z-Score",
      accessorFn: getZScore,
      cell: (info) => formatZScore(info.getValue()),
    },
    {
      id: "percentile_within_tag",
      header: "Percentile",
      accessorFn: (row) => getNumber(row.percentile_within_tag),
      cell: (info) => getPercentileDisplay(info.row.original),
    },
    {
      id: "rank_within_tag_by_z",
      header: "Rank",
      accessorFn: getRankByZ,
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "trend",
      header: "Trend",
      accessorFn: getTrendSortValue,
      enableSorting: false,
      cell: (info) =>
        React.createElement(TrendBadge, {
          row: info.row.original,
          compact: true,
        }),
    },
  ];
}

export function createTagExplorerColumns() {
  return [
    {
      id: "rank_within_tag_by_z",
      header: "Rank",
      accessorFn: getRankByZ,
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "commander_name",
      header: "Commander",
      accessorFn: getCommanderName,

      /*
        Same clickable commander-name cell for the Tag Explorer.
      */
      cell: (info) =>
        React.createElement(CommanderName, {
          commander: info.row.original,
          className: "table-commander-link",
        }),
    },
    {
      id: "color_identity",
      header: "Colors",
      accessorFn: getSortableColorIdentity,
      sortingFn: compareColorIdentities,
      sortUndefined: "last",
      sortDescFirst: false,
      cell: (info) => React.createElement(CommanderColors, { colorIdentity: info.getValue() }),
    },
    {
      id: "z",
      header: "Z-Score",
      accessorFn: getZScore,
      cell: (info) => formatZScore(info.getValue()),
    },
    {
      id: "affinity_pct",
      header: "Affinity %",
      accessorFn: getAffinityDisplayPct,
      cell: (info) => formatPercentFromDisplayValue(info.getValue()),
    },
    {
      id: "tag_decks",
      header: "Tag Decks",
      accessorFn: getTagDecks,
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "total_decks",
      header: "Total Decks",
      accessorFn: getTotalDecks,
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "percentile_within_tag",
      header: "Percentile",
      accessorFn: (row) => getNumber(row.percentile_within_tag),
      cell: (info) => getPercentileDisplay(info.row.original),
    },
    {
      id: "tag_mean_pct",
      header: "Tag Mean %",
      accessorFn: getTagMeanPct,
      cell: (info) => formatPercentFromDecimal(info.getValue()),
    },
    {
      id: "tag_std_pct",
      header: "Tag Std. Dev.",
      accessorFn: getTagStdPct,
      cell: (info) => formatPercentFromDecimal(info.getValue()),
    },
    {
      id: "rank_within_tag_by_pct",
      header: "Rank by %",
      accessorFn: getRankByPct,
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "rank_within_tag_by_tag_decks",
      header: "Rank by Tag Decks",
      accessorFn: getRankByTagDecks,
      cell: (info) => formatNumber(info.getValue()),
    },
    {
      id: "rank_delta",
      header: "Rank Move",
      accessorFn: getRankDelta,
      cell: (info) => formatDecimal(info.getValue(), 0),
    },
    {
      id: "trend",
      header: "Trend",
      accessorFn: getTrendSortValue,
      enableSorting: false,
      cell: (info) =>
        React.createElement(TrendBadge, {
          row: info.row.original,
          compact: true,
        }),
    },
  ];
}
