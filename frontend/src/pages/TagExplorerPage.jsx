/*
  TagExplorerPage.jsx

  The tag explorer answers:

    "Within one selected tag, which commanders are most unusually associated
     with that tag?"

  It loads:
  - tag_rankings.json for commander rows
  - tag_summary.json for summary stats and the tag dropdown
*/

import { useEffect, useMemo, useState } from "react";

import DataTable from "../components/DataTable";
import SmallBarChart from "../components/SmallBarChart";
import TableFilters from "../components/TableFilters";
import TagSummaryCard from "../components/TagSummaryCard";

import { createTagExplorerColumns } from "../lib/tableColumns";

import {
  buildTagOptionsFromRows,
  buildTagOptionsFromSummary,
  findTagSummary,
  formatNumber,
  getSelectedTagName,
  makeTopTagDeckChartData,
  makeTopZChartData,
  rowMatchesTagExplorerFilters,
} from "../lib/filterUtils";

const TAG_RANKINGS_PATH = "/data/latest/tag_rankings.json";
const TAG_SUMMARY_PATH = "/data/latest/tag_summary.json";

async function loadJson(path) {
  const response = await fetch(path);

  if (!response.ok) {
    throw new Error(`Could not load ${path}. HTTP status: ${response.status}`);
  }

  return response.json();
}

export default function TagExplorerPage() {
  const [rows, setRows] = useState([]);
  const [tagSummaryRows, setTagSummaryRows] = useState([]);
  const [selectedTagSlug, setSelectedTagSlug] = useState("");

  const [loadState, setLoadState] = useState({
    loading: true,
    error: "",
  });

  const [filters, setFilters] = useState({
    commanderText: "",
    colorIdentity: "",
    minTotalDecks: "200",
    minTagDecks: "5",
    minZ: "",
    minAffinityPct: "",
    trendStatus: "",
  });

  useEffect(() => {
    let isMounted = true;

    async function loadTagData() {
      try {
        const [loadedRows, loadedTagSummaryRows] = await Promise.all([
          loadJson(TAG_RANKINGS_PATH),
          loadJson(TAG_SUMMARY_PATH),
        ]);

        if (!Array.isArray(loadedRows)) {
          throw new Error("tag_rankings.json did not contain an array.");
        }

        if (!Array.isArray(loadedTagSummaryRows)) {
          throw new Error("tag_summary.json did not contain an array.");
        }

        if (isMounted) {
          setRows(loadedRows);
          setTagSummaryRows(loadedTagSummaryRows);
          setLoadState({
            loading: false,
            error: "",
          });
        }
      } catch (error) {
        if (isMounted) {
          setLoadState({
            loading: false,
            error: error.message,
          });
        }
      }
    }

    loadTagData();

    return () => {
      isMounted = false;
    };
  }, []);

  const columns = useMemo(() => createTagExplorerColumns(), []);

  const tagOptions = useMemo(() => {
    const summaryOptions = buildTagOptionsFromSummary(tagSummaryRows);

    if (summaryOptions.length > 0) {
      return summaryOptions;
    }

    return buildTagOptionsFromRows(rows);
  }, [tagSummaryRows, rows]);

  /*
    Once data loads, automatically select the first tag alphabetically.
    This gives the page useful content before the user manually chooses a tag.
  */
  useEffect(() => {
    if (!selectedTagSlug && tagOptions.length > 0) {
      setSelectedTagSlug(tagOptions[0].slug);
    }
  }, [selectedTagSlug, tagOptions]);

  const selectedTagName = useMemo(() => {
    return getSelectedTagName(tagOptions, selectedTagSlug);
  }, [tagOptions, selectedTagSlug]);

  const selectedTagSummary = useMemo(() => {
    return findTagSummary(tagSummaryRows, selectedTagSlug);
  }, [tagSummaryRows, selectedTagSlug]);

  const selectedTagRows = useMemo(() => {
    if (!selectedTagSlug) {
      return [];
    }

    return rows.filter((row) => {
      return (
        row.tag_slug === selectedTagSlug ||
        row.slug === selectedTagSlug ||
        row.tag_name === selectedTagSlug
      );
    });
  }, [rows, selectedTagSlug]);

  const filteredRows = useMemo(() => {
    return selectedTagRows.filter((row) =>
      rowMatchesTagExplorerFilters(row, filters)
    );
  }, [selectedTagRows, filters]);

  const zChartData = useMemo(() => {
    return makeTopZChartData(filteredRows, 10);
  }, [filteredRows]);

  const tagDeckChartData = useMemo(() => {
    return makeTopTagDeckChartData(filteredRows, 10);
  }, [filteredRows]);

  if (loadState.loading) {
    return (
      <main className="page">
        <p>Loading tag explorer...</p>
      </main>
    );
  }

  if (loadState.error) {
    return (
      <main className="page">
        <h1>Tag Explorer</h1>
        <p className="error-message">{loadState.error}</p>
        <p>
          Check that <code>{TAG_RANKINGS_PATH}</code> and{" "}
          <code>{TAG_SUMMARY_PATH}</code> exist inside{" "}
          <code>frontend/public/data/latest/</code>.
        </p>
      </main>
    );
  }

  return (
    <main className="page">
      <section className="page-header">
        <p className="eyebrow">Chat 10</p>
        <h1>Tag Explorer</h1>
        <p>
          Select one tag and inspect which commanders rank highest within that
          tag by z-score, affinity percentage, tag deck count, percentile, and
          rank.
        </p>
      </section>

      <section className="tag-selector-panel">
        <label>
          <span>Selected tag</span>
          <select
            value={selectedTagSlug}
            onChange={(event) => setSelectedTagSlug(event.target.value)}
          >
            {tagOptions.map((tag) => (
              <option key={tag.slug} value={tag.slug}>
                {tag.name}
              </option>
            ))}
          </select>
        </label>
      </section>

      <TagSummaryCard
        tagSummary={selectedTagSummary}
        selectedTagName={selectedTagName}
      />

      <section className="stat-row">
        <article className="stat-card">
          <span>Rows for selected tag</span>
          <strong>{formatNumber(selectedTagRows.length)}</strong>
        </article>

        <article className="stat-card">
          <span>Rows after filters</span>
          <strong>{formatNumber(filteredRows.length)}</strong>
        </article>

        <article className="stat-card">
          <span>Total tags loaded</span>
          <strong>{formatNumber(tagOptions.length)}</strong>
        </article>
      </section>

      <TableFilters
        mode="tag"
        filters={filters}
        setFilters={setFilters}
        tagOptions={tagOptions}
      />

      <div className="chart-grid">
        <SmallBarChart
          title={`Top z-scores for ${selectedTagName}`}
          description="Shows the strongest statistical affinities within the selected tag."
          data={zChartData}
          xKey="name"
          yKey="value"
        />

        <SmallBarChart
          title={`Top tag deck counts for ${selectedTagName}`}
          description="Shows commanders with the largest raw number of decks in this tag."
          data={tagDeckChartData}
          xKey="name"
          yKey="value"
        />
      </div>

      <DataTable
        data={filteredRows}
        columns={columns}
        tableLabel={`Tag explorer table for ${selectedTagName}`}
        initialSorting={[{ id: "z", desc: true }]}
        pageSize={25}
      />
    </main>
  );
}