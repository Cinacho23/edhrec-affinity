import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { getBuildCollectionProgress, getBuildSourceFreshness, getCurrentBuildDiscoveryCommanders } from "../lib/buildStyleUtils";
import { formatCollectionDuration, getBuildCollectorExecutionStatus, getBuildCollectorStatus } from "../lib/buildCollectionStatus";
import { watchBuildDiscovery } from "../lib/buildDiscoveryRefresh";
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

export default function BuildDiscoveryPage() {
  const [state, setState] = useState({ loading: true, commanders: [], index: null, error: null });
  const [execution, setExecution] = useState(null);
  const [query, setQuery] = useState("");
  useEffect(() => watchBuildDiscovery({
    onIndex: (index) => setState({ loading: false, commanders: getCurrentBuildDiscoveryCommanders(index), index, error: null, refreshError: null }),
    onExecution: setExecution,
    onIndexError: (error) => setState((previous) => previous.index
      ? { ...previous, refreshError: error.message }
      : { ...previous, loading: false, error: error.message }),
  }), []);

  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return state.commanders.filter((commander) => String(commander.commander_name || commander.commander_slug).toLowerCase().includes(normalized))
      .toSorted((left, right) => String(left.commander_name || left.commander_slug).localeCompare(String(right.commander_name || right.commander_slug)));
  }, [state.commanders, query]);

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
      <div className="search-panel build-discovery-search">
        <label htmlFor="build-discovery-search">Search current collector reports</label>
        <input id="build-discovery-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search commander name…" />
        <p className="muted" aria-live="polite">{formatNumber(visible.length)} of {formatNumber(state.commanders.length)} reports</p>
      </div>
      <div className="build-discovery-grid">{visible.map((commander) => {
        const freshness = getBuildSourceFreshness(commander.collection);
        const progress = getBuildCollectionProgress(commander.collection);
        return <article className="panel build-discovery-card" key={commander.commander_slug}>
        <h2><Link to={`/commanders/${encodeURIComponent(commander.commander_slug)}`}>{commander.commander_name || commander.commander_slug}</Link></h2>
        <div className="build-discovery-card__stats">
          <div><strong>{formatNumber(commander.package_count)}</strong><span>{commander.package_count === 1 ? "suggested package" : "suggested packages"}</span></div>
          <div><strong>{formatNumber(commander.profile_count)}</strong><span>tag profiles</span></div>
          <div><strong>{formatNumber(commander.unique_decks)}</strong><span>analyzed decks</span></div>
        </div>
        {commander.status !== "fitted" ? <p className="muted">{commander.profile_count > 0
          ? "Tag profile comparisons available; deck evidence is insufficient to fit packages."
          : "Insufficient data for tag comparisons or packages."}</p> : null}
        <div className="build-discovery-card__sources">
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
      {!visible.length ? <p className="muted">No available reports match this search.</p> : null}
    </> : <div className="panel"><p className="muted">Waiting for the first completed report since the fresh start from a commander with at least {formatNumber(minimumDecks)} total EDHREC decks. New reports will appear as collection and analysis finish.</p><Link className="build-discovery-all-commanders" to="/commanders">Browse all commanders →</Link></div>}
  </section>;
}
