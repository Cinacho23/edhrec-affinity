import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import BuildDiscoveryCards from "../components/BuildDiscoveryCards";
import CommanderName from "../components/CommanderName";
import { loadCommanderIndex } from "../lib/api";
import { getCommanderCards } from "../lib/commanderPreview";
import { getBuildCollectionProgress, getBuildSourceFreshness, getBuildThemeEvidence, getCurrentBuildDiscoveryCommanders, getPackageBracketLabel, getSupportedThemes } from "../lib/buildStyleUtils";
import { formatCollectionDuration, getBuildCollectorExecutionStatus, getBuildCollectorStatus } from "../lib/buildCollectionStatus";
import { watchBuildDiscovery } from "../lib/buildDiscoveryRefresh";
import {
  BUILD_DISCOVERY_SORT_OPTIONS,
  DEFAULT_BUILD_DISCOVERY_FILTERS,
  buildDiscoverySearchParams,
  filterBuildDiscoveryCommanders,
  getBuildDiscoveryFilterErrors,
  getBuildDiscoveryPackageTags,
  matchesBuildDiscoveryPackageTag,
  readBuildDiscoveryFilters,
} from "../lib/buildDiscoveryFilters";
import { formatNumber } from "../lib/formatters";
import "./BuildDiscoveryPage.css";

function reportDate(value) {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime())
    ? date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "America/Toronto" })
    : "Date unavailable";
}

function collectorDate(value) {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime())
    ? date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Toronto", timeZoneName: "short" })
    : "Date unavailable";
}

const COUNT_FILTERS = [
  { title: "Auto-selected packages", minKey: "minPackages", maxKey: "maxPackages" },
  { title: "Analyzed unique decks", minKey: "minAnalyzed", maxKey: "maxAnalyzed" },
  { title: "Total EDHREC decks", minKey: "minTotalDecks", maxKey: "maxTotalDecks" },
  { title: "Tag profiles", minKey: "minProfiles", maxKey: "maxProfiles" },
];
const PAGE_SIZE = 50;

function CountRange({ title, minKey, maxKey, filters, onChange }) {
  return <fieldset className="build-discovery-range">
    <legend>{title}</legend>
    <div>
      <label htmlFor={`build-discovery-${minKey}`}>Minimum
        <input id={`build-discovery-${minKey}`} aria-label={`Minimum ${title.toLowerCase()}`} type="number" min="0" step="1" inputMode="numeric" placeholder="Any" value={filters[minKey]} onChange={(event) => onChange(minKey, event.target.value)} />
      </label>
      <label htmlFor={`build-discovery-${maxKey}`}>Maximum
        <input id={`build-discovery-${maxKey}`} aria-label={`Maximum ${title.toLowerCase()}`} type="number" min="0" step="1" inputMode="numeric" placeholder="Any" value={filters[maxKey]} onChange={(event) => onChange(maxKey, event.target.value)} />
      </label>
    </div>
  </fieldset>;
}

function PackageSummaries({ commander, selectedTag }) {
  if (!Array.isArray(commander.packages)) {
    return commander.package_count > 0 ? <p className="muted build-discovery-card__package-note">Package tags and brackets are unavailable in this summary. Open the report for details.</p> : null;
  }
  if (!commander.packages.length) return null;
  return <div className="build-discovery-card__packages">
    <p className="build-discovery-card__package-label">Auto-selected packages · suggested tags</p>
    <ul>{commander.packages.map((pkg, index) => {
      const tags = getSupportedThemes(pkg);
      const bracket = getPackageBracketLabel(pkg.generated_deck);
      return <li key={pkg.id ?? index}>
        <div className="build-discovery-card__package-heading"><strong>Package {index + 1}</strong><span className={bracket ? "build-discovery-card__bracket" : "muted"}>{bracket || "Bracket unavailable"}</span></div>
        <div className="build-discovery-card__tags">{tags.length ? tags.map((tag) => {
          const matched = selectedTag && matchesBuildDiscoveryPackageTag(tag, selectedTag);
          const evidence = getBuildThemeEvidence(tag);
          return <span key={tag.tag_slug || tag.tag_name} className={matched ? "build-discovery-card__tag--matched" : undefined}
            data-support-tier={evidence.tier || undefined} title={`${matched ? "Matches selected package tag. " : ""}${evidence.tooltip}`}>
            {tag.tag_name || tag.tag_slug}{evidence.note ? <small> · {evidence.note}</small> : null}
          </span>;
        }) : <span className="muted">No supported tag label</span>}</div>
      </li>;
    })}</ul>
  </div>;
}

export default function BuildDiscoveryPage() {
  const [state, setState] = useState({ loading: true, commanders: [], index: null, error: null });
  const [execution, setExecution] = useState(null);
  const [cardCatalog, setCardCatalog] = useState(new Map());
  const catalogRequest = useRef(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => readBuildDiscoveryFilters(searchParams), [searchParams]);
  const pendingFilters = useRef(filters);
  const [page, setPage] = useState(1);
  useEffect(() => { pendingFilters.current = filters; }, [filters]);
  useEffect(() => watchBuildDiscovery({
    onIndex: (index) => setState({ loading: false, commanders: getCurrentBuildDiscoveryCommanders(index), index, error: null, refreshError: null }),
    onExecution: setExecution,
    onIndexError: (error) => setState((previous) => previous.index
      ? { ...previous, refreshError: error.message }
      : { ...previous, loading: false, error: error.message }),
  }), []);
  useEffect(() => {
    // Older discovery indexes omit images. One local catalog request supplies
    // all visible tiles without fetching a separate report for each commander.
    if (!state.commanders.some((commander) =>
      !getCommanderCards(commander).some((card) => card.faces.length))) return;
    let active = true;
    catalogRequest.current ||= loadCommanderIndex();
    catalogRequest.current.then((data) => {
      const rows = Array.isArray(data) ? data : data?.rows || [];
      if (active) setCardCatalog(new Map(rows.map((row) => [row.commander_slug, row])));
    }).catch(() => { /* Card previews can still resolve lazily when opened. */ });
    return () => { active = false; };
  }, [state.commanders]);

  const filterErrors = getBuildDiscoveryFilterErrors(filters);
  const packageTags = useMemo(() => getBuildDiscoveryPackageTags(state.commanders), [state.commanders]);
  const selectedPackageTag = filters.packageTag && (packageTags.find((tag) => tag.value === filters.packageTag)
    || packageTags.find((tag) => matchesBuildDiscoveryPackageTag({ tag_slug: tag.value, tag_name: tag.label }, filters.packageTag)));
  const matching = useMemo(() => filterBuildDiscoveryCommanders(state.commanders, filters), [state.commanders, filters]);
  const pageCount = Math.max(1, Math.ceil(matching.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const firstIndex = (currentPage - 1) * PAGE_SIZE;
  const visible = matching.slice(firstIndex, firstIndex + PAGE_SIZE);
  const hasChanges = Object.entries(DEFAULT_BUILD_DISCOVERY_FILTERS).some(([key, value]) => filters[key] !== value);
  const textualSort = filters.sortBy === "commander_name";
  const dateSort = filters.sortBy === "generated_at";

  function updateFilter(key, value) {
    // Router navigation can commit after the next control changes. Merge into
    // the latest selection so quick edits cannot overwrite another field.
    pendingFilters.current = { ...pendingFilters.current, [key]: value };
    setSearchParams(buildDiscoverySearchParams(pendingFilters.current), { replace: true });
    setPage(1);
  }

  function resetFilters() {
    pendingFilters.current = DEFAULT_BUILD_DISCOVERY_FILTERS;
    setSearchParams({}, { replace: true });
    setPage(1);
  }

  const scopedMinimum = state.index?.discovery?.min_total_decks;
  const scopeKnown = Number.isSafeInteger(scopedMinimum) && scopedMinimum >= 200;
  const minimumDecks = scopeKnown ? scopedMinimum : 200;
  const totalCatalog = scopeKnown && Number.isInteger(state.index?.total_catalog) && state.index.total_catalog >= state.commanders.length
    ? state.index.total_catalog : null;
  const pending = totalCatalog === null ? null : totalCatalog - state.commanders.length;
  const collector = getBuildCollectorStatus(state.index?.collection_progress);
  const activity = getBuildCollectorExecutionStatus(execution, collector?.updatedAt);
  const freshnessCounts = state.commanders.reduce((counts, commander) => {
    counts[getBuildSourceFreshness(commander.collection).status] += 1;
    if (getBuildCollectionProgress(commander.collection).targetMet) counts.targetMet += 1;
    return counts;
  }, { fresh: 0, mixed: 0, stale: 0, unknown: 0, targetMet: 0 });

  return <section className="page build-discovery-page">
    <div className="page-header">
      <p className="eyebrow">Exploratory analysis</p>
      <h1>Build discovery</h1>
      <p>Explore the different ways a commander gets built. Compare tag-specific card profiles and recurring card packages learned from individual decklists, including decks with no tags.</p>
    </div>

    <div className="panel build-discovery-coverage">
      <h2>Current collector reports</h2>
      <p>Only commanders with at least {formatNumber(minimumDecks)} total EDHREC decks are analyzed. This threshold uses the commander’s total deck population, rather than the number already collected or kept for analysis.</p>
      <p>These reports use decklists collected since the fresh start. The list grows as eligible commanders finish collection and analysis. The package count adapts to the evidence, and a package can suggest one or several themes.</p>
      <p>Collection continues through every available deck preview and adapts its operating rate when EDHREC throttles requests. Source-check age and full-index coverage are shown separately.</p>
      <p>A package describes a recurring card pattern across decks; the generated deck under it is one 100-card example of that pattern. Changing the package count fits a different model.</p>
      {!state.loading && !state.error ? <>
        <strong>{totalCatalog === null
          ? `${formatNumber(state.commanders.length)} ${state.commanders.length === 1 ? "current collector report" : "current collector reports"}`
          : `${formatNumber(state.commanders.length)} of ${formatNumber(totalCatalog)} eligible commanders have current collector reports`}</strong>
        {pending !== null ? <p className="build-discovery-coverage__pending">{formatNumber(pending)} eligible {pending === 1 ? "commander awaits" : "commanders await"} a current collector report. Completed collections stay listed when there is insufficient data to fit packages.</p> : null}
        <p className="build-discovery-coverage__freshness">{formatNumber(freshnessCounts.targetMet)}{totalCatalog !== null ? ` of ${formatNumber(totalCatalog)}` : ""} commanders have full index coverage with recent source checks. {formatNumber(freshnessCounts.unknown)} shown reports have unknown source age; {formatNumber(freshnessCounts.mixed + freshnessCounts.stale)} contain source checks needing refresh.</p>
      </> : null}
    </div>

    {!state.loading && !state.error ? <div className="panel build-discovery-collector">
      <h2>{activity?.label || collector?.label || "Collection progress"}</h2>
      {activity ? <p>{activity.queued ? "Queued" : activity.running ? "Started" : "Run started"} {collectorDate(activity.activityAt)}. <a href={activity.url} target="_blank" rel="noopener noreferrer">View collector run ↗</a>. {activity.running
        ? "The collector workflow is active. Report counts and timing estimates below come from the last published batch."
        : activity.queued ? "Report counts will update after the next batch is collected and published."
          : "Published counts may still be waiting for this run’s results."}</p> : null}
      {collector ? <>
        <p className="muted">Last published batch {collectorDate(collector.updatedAt)}{collector.stale ? " · awaiting a newer published batch" : ""}. This page checks for updates every two minutes while visible.</p>
        <div className="build-discovery-collector__stats">
          <div><strong>{collector.learned ? `${collector.operatingRate.toLocaleString("en-US", { maximumFractionDigits: 3 })} / sec` : "Calibrating"}</strong><span>{collector.learned ? "provisional operating ceiling" : "operating rate"}</span></div>
          <div><strong>{collector.remaining === null ? "Unknown" : formatNumber(collector.remaining)}</strong><span>estimated requests remaining</span></div>
          <div><strong>{collector.learned ? formatCollectionDuration(collector.minimumSeconds) : "Awaiting calibration"}</strong><span>minimum time at this ceiling, including cooldown</span></div>
          <div><strong>{formatCollectionDuration(collector.projectedSeconds)}</strong><span>{collector.paused ? "projection after the recorded pause" : "projection at the observed rate"}</span></div>
        </div>
        <p>The provisional operating ceiling is estimated below observed throttling and may adjust again. {collector.configuredRate !== null ? `The configured maximum is ${collector.configuredRate.toLocaleString("en-US")} requests per second; it is not a measured source limit. ` : ""}{collector.observedRate !== null ? `The last batch averaged ${collector.observedRate.toLocaleString("en-US", { maximumFractionDigits: 3 })} successful responses per second. ` : ""}Timing estimates exclude future runner gaps and can grow with retries, analysis, or newly discovered decks.</p>
        {collector.observedRateAt ? <p>Observed rate measured {collectorDate(collector.observedRateAt)}.</p> : null}
        {!collector.inventoryComplete ? <p>The deck inventory is still incomplete, so estimates may increase.</p> : null}
        {collector.blockedUntil && !activity?.running ? <p>The last published batch recorded a source cooldown until {collectorDate(collector.blockedUntil)}. Estimates assume collection can resume after this cooldown.</p>
          : collector.paused ? <p>The last published batch stopped during a source cooldown or collection pause. Its estimates may change as newer batches are published.</p> : null}
        {collector.projectedFinishAt ? <p>Estimated finish at the last observed rate{collector.paused ? ", assuming requests resume after the recorded pause" : ""}: {collectorDate(collector.projectedFinishAt)}. This is an estimate from the checkpoint above.</p> : null}
      </> : <p className="muted">The next collector checkpoint will publish its learned operating rate, remaining work, and timing estimates here.</p>}
      {state.refreshError ? <p role="status">Published reports could not be refreshed. Showing the last available data and checking again automatically.</p> : null}
    </div> : null}

    {state.loading ? <p className="muted" role="status">Loading available build reports…</p> : state.error ? <div className="panel"><p className="error-message">Could not load build discovery: {state.error}</p></div> : state.commanders.length ? <>
      <section className="filter-panel build-discovery-filters" aria-labelledby="build-discovery-filter-heading">
        <div className="filter-panel-header">
          <div><h2 id="build-discovery-filter-heading">Find build reports</h2><p className="muted">Combine filters across all current reports. Leave a bound blank for any value; use the same minimum and maximum for an exact count.</p></div>
          <button type="button" onClick={resetFilters} disabled={!hasChanges}>Reset filters</button>
        </div>
        <div className="filter-grid build-discovery-filters__search">
          <label htmlFor="build-discovery-search">Commander name
            <input id="build-discovery-search" type="search" value={filters.query} onChange={(event) => updateFilter("query", event.target.value)} placeholder="Search commander name…" />
          </label>
          <label htmlFor="build-discovery-sort">Sort by
            <select id="build-discovery-sort" aria-label="Sort by" value={filters.sortBy} onChange={(event) => updateFilter("sortBy", event.target.value)}>
              {BUILD_DISCOVERY_SORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label htmlFor="build-discovery-direction">Order
            <select id="build-discovery-direction" aria-label="Order" value={filters.direction} onChange={(event) => updateFilter("direction", event.target.value)}>
              <option value="desc">{textualSort ? "Z–A" : dateSort ? "Newest first" : "Highest first"}</option>
              <option value="asc">{textualSort ? "A–Z" : dateSort ? "Oldest first" : "Lowest first"}</option>
            </select>
          </label>
        </div>
        <div className="build-discovery-filters__package-tag">
          <label htmlFor="build-discovery-package-tag">Package tag
            <select id="build-discovery-package-tag" aria-describedby="build-discovery-package-tag-note" value={selectedPackageTag?.value || filters.packageTag} onChange={(event) => updateFilter("packageTag", event.target.value)}>
              <option value="">All package tags</option>
              {filters.packageTag && !selectedPackageTag ? <option value={filters.packageTag}>{filters.packageTag} · unavailable in current reports</option> : null}
              {packageTags.map((tag) => <option key={tag.value} value={tag.value}>{tag.label} ({formatNumber(tag.count)} {tag.count === 1 ? "commander" : "commanders"})</option>)}
            </select>
          </label>
          <p id="build-discovery-package-tag-note" className="muted">Show commanders with this suggested tag on at least one auto-selected package. Their other packages remain visible.</p>
        </div>
        <div className="build-discovery-filters__ranges">
          {COUNT_FILTERS.map((range) => <CountRange key={range.minKey} {...range} filters={filters} onChange={updateFilter} />)}
        </div>
        <div className="filter-grid">
          <label htmlFor="build-discovery-status">Analysis result
            <select id="build-discovery-status" aria-label="Analysis result" value={filters.status} onChange={(event) => updateFilter("status", event.target.value)}>
              <option value="all">All reports</option><option value="fitted">Packages discovered</option><option value="insufficient_data">Insufficient usable evidence</option>
            </select>
          </label>
          <label htmlFor="build-discovery-coverage">Deck index coverage
            <select id="build-discovery-coverage" aria-label="Deck index coverage" value={filters.coverage} onChange={(event) => updateFilter("coverage", event.target.value)}>
              <option value="all">Any coverage</option><option value="complete">Full index checked</option><option value="partial">Partial index coverage</option><option value="unknown">Coverage unknown</option>
            </select>
          </label>
          <label htmlFor="build-discovery-freshness">Source checks
            <select id="build-discovery-freshness" aria-label="Source checks" value={filters.freshness} onChange={(event) => updateFilter("freshness", event.target.value)}>
              <option value="all">Any source age</option><option value="fresh">Recently checked</option><option value="needs_refresh">Need refresh or incomplete dates</option><option value="unknown">Source age unknown</option>
            </select>
          </label>
        </div>
        <p className="build-discovery-filters__note muted">Analyzed decks are usable unique lists; total decks are EDHREC’s commander population. Tag profiles count the source tag pages compared. Coverage measures previews checked, independently of source age. Filters stay in the page URL when you open a report or share this view.</p>
        {filterErrors.length ? <div className="error-message" role="alert">{filterErrors.map((error) => <p key={error}>{error}</p>)}</div> : null}
      </section>
      <div className="build-discovery-results">
        <p className="muted" aria-live="polite">{matching.length ? `Showing ${formatNumber(firstIndex + 1)}–${formatNumber(firstIndex + visible.length)} of ${formatNumber(matching.length)} matching reports` : "0 matching reports"} · {formatNumber(state.commanders.length)} current reports available</p>
        {pageCount > 1 ? <nav className="build-discovery-pagination" aria-label="Build report pages">
          <button type="button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>Previous</button>
          <span>Page {formatNumber(currentPage)} of {formatNumber(pageCount)}</span>
          <button type="button" disabled={currentPage >= pageCount} onClick={() => setPage(currentPage + 1)}>Next</button>
        </nav> : null}
      </div>
      <div className="build-discovery-grid">{visible.map((commander) => {
        const freshness = getBuildSourceFreshness(commander.collection);
        const progress = getBuildCollectionProgress(commander.collection);
        const cardDetails = { ...cardCatalog.get(commander.commander_slug), ...commander };
        return <article className="panel build-discovery-card" key={commander.commander_slug}>
        <h2><CommanderName commander={cardDetails} className="build-discovery-card__name" /></h2>
        <BuildDiscoveryCards commander={cardDetails} />
        <div className="build-discovery-card__stats">
          <div><strong>{formatNumber(commander.package_count)}</strong><span>Auto-selected {commander.package_count === 1 ? "package" : "packages"}</span></div>
          <div><strong>{formatNumber(commander.profile_count)}</strong><span>tag profiles</span></div>
          <div><strong>{formatNumber(commander.unique_decks)}</strong><span>analyzed decks</span></div>
        </div>
        <PackageSummaries commander={commander} selectedTag={filters.packageTag} />
        {commander.status !== "fitted" ? <p className="muted">{commander.profile_count > 0
          ? "Tag profile comparisons available; deck evidence is insufficient to fit packages."
          : "Insufficient data for tag comparisons or packages."}</p> : null}
        <div className="build-discovery-card__sources">
          <span>{formatNumber(commander.total_edhrec_decks)} total EDHREC decks</span>
          <strong>{progress.targetMet ? "Full index checked recently" : progress.label}</strong>
          {!progress.analysisCurrent ? <span>Analysis awaiting the current deck collection</span> : null}
          {progress.known ? <span>{formatNumber(progress.attempted)} / {formatNumber(progress.frame)} previews attempted</span> : null}
          {progress.unavailable > 0 ? <span>{formatNumber(progress.unavailable)} previews unavailable or unreadable</span> : null}
          <span>{freshness.label}{freshness.versionsVerified && freshness.oldestVerifiedAt
            ? ` · oldest verification ${reportDate(freshness.oldestVerifiedAt)}`
            : freshness.oldestSourceAt ? ` · oldest download ${reportDate(freshness.oldestSourceAt)}` : ""}</span>
        </div>
        <div className="build-discovery-card__footer"><span>Analysis built {reportDate(commander.generated_at)}</span><Link to={`/commanders/${encodeURIComponent(commander.commander_slug)}`}>Explore report →</Link></div>
      </article>; })}</div>
      {!matching.length ? <div className="panel build-discovery-empty"><p className="muted">{filterErrors.length ? "Correct the filter ranges to see matching reports." : "No current reports match these filters. Try another package tag, widen a range, or clear the search."}</p><button type="button" onClick={resetFilters}>Reset filters</button></div> : null}
    </> : <div className="panel"><p className="muted">Waiting for the first completed report since the fresh start from a commander with at least {formatNumber(minimumDecks)} total EDHREC decks. New reports will appear as collection and analysis finish.</p><Link className="build-discovery-all-commanders" to="/commanders">Browse all commanders →</Link></div>}
  </section>;
}
