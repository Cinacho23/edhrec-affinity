import { useEffect, useState } from "react";

import { loadCommanderBuildStyles } from "../lib/api";
import {
  formatBuildPoints,
  formatBuildLift,
  formatBuildShare,
  generatedDeckFilename,
  getBuildCollectionProgress,
  getBuildModel,
  getBuildModelOptions,
  getBuildSourceFreshness,
  getBuildThemeEvidence,
  getComparableProfilePairs,
  getDeckMixtureRows,
  getExternalDeckRating,
  getExternalDeckRestrictionCheck,
  getGeneratedDeckBracketRepair,
  getGeneratedDeckColorChoices,
  getGeneratedDeckDistinctiveCards,
  getGeneratedDeckExport,
  getGeneratedDeckGroups,
  getGeneratedDeckPowerAlignment,
  getGeneratedDeckPowerRating,
  getPackageBracketLabel,
  getSupportedThemes,
  hasBuildStylePopulation,
  isBuildMetadataFresh,
  safeExternalUrl,
  splitPrimerCardReferences,
} from "../lib/buildStyleUtils";
import { formatDecimal, formatNumber } from "../lib/formatters";
import CommanderName from "./CommanderName";
import "./CommanderBuildStyles.css";

function SourceLink({ url, children }) {
  const href = safeExternalUrl(url);
  return href ? <a href={href} target="_blank" rel="noreferrer">{children} ↗</a> : null;
}

function DeckCardName({ name }) {
  return <CommanderName commander={{ commander_name: name, commander_cards: [{ name }] }} linked={false} className="build-card-name" />;
}

function downloadDeckText(text, name, extension) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = generatedDeckFilename(name, extension);
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

const VALIDATION_LABELS = {
  card_count: "100 cards", commander_identity: "Correct commander", color_identity: "Color identity",
  commander_color_choices: "Chosen commander colors",
  singleton: "Singleton", commander_legality: "Commander legality", sampled_pool: "Sampled card pool",
  metadata_complete: "Card metadata", metadata_fresh: "Recent card rules cache",
};

function checkedDate(value) {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime())
    ? date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "America/Toronto" })
    : "Date unavailable";
}

function ExternalDeckRating({ rating, deckHash }) {
  const result = getExternalDeckRating(rating, undefined, deckHash);
  if (!result) return <p className="build-note"><strong>External rating: unverified.</strong> Use the EDHPowerLevel link to assess this exact card list.</p>;
  const stale = result.status === "stale";
  return <div className="build-external-rating" data-status={result.status}>
    <p><strong>EDHPowerLevel result{stale ? " · historical observation" : " · verified for this card list"}</strong><span>Checked {checkedDate(result.checked_at)}</span></p>
    <div className="build-generated-deck__metrics">
      {result.scoreBracket !== null ? <span><strong>{result.scoreBracket}</strong> {stale ? "historical" : "calculated"} power bracket</span> : null}
      {result.power_level !== undefined && result.power_level !== null ? <span><strong>{String(result.power_level)}</strong> {stale ? "historical" : result.scoreBracket === null ? "reported" : "calculated"} power</span> : null}
      {result.playabilityKnown ? <span><strong>{formatDecimal(result.playability_pct, 1)}%</strong> {stale ? "historical " : ""}playability</span> : null}
    </div>
    {result.bracketMismatch ? <p className="build-generated-deck__review-note">{stale ? "At that check, the reported" : "The reported"} restriction bracket was {result.reportedBracket}, while the power score corresponds to bracket {result.scoreBracket}.{stale
      ? " This observation does not establish the current deck’s bracket."
      : result.reportedBracket > result.scoreBracket ? " The card list needs adjustment to match its calculated power bracket." : " The displayed bracket follows the calculated power score."}</p> : null}
    <p className="build-note">{stale
      ? "Recheck this older observation before treating its score as current. " : ""}{result.playabilityKnown
      ? `72.5–77.5% playability target ${stale ? `was ${result.inTargetRange ? "met" : "not met"} at that check` : result.targetMet ? "met" : "not met"}. `
      : "Playability was not recorded. "}The displayed power bracket uses score thresholds 4.7, 6.7, 7.7 and 9.25. Card restrictions are checked separately.</p>
    {result.source_url ? <p className="build-source-links"><SourceLink url={result.source_url}>Open rated card list</SourceLink></p> : null}
  </div>;
}

const RESTRICTION_LABELS = {
  game_changers: "Game Changers", extra_turns: "All extra-turn cards",
  chaining_extra_turns: "Chaining extra turns", mass_land_denial: "Mass land denial",
  early_combos: "Early combos", late_combos: "Late combos",
};

function OfficialRestrictionCheck({ deck }) {
  const check = getExternalDeckRestrictionCheck(deck);
  if (!check) return null;
  return <div className="build-official-restrictions" data-status={check.status}>
    <p><strong>{check.label}.</strong>{check.checkedAt ? <span> Checked {checkedDate(check.checkedAt)}.</span> : null}</p>
    {check.status === "verified" ? <p className="build-note">The complete EDHPowerLevel analysis covered mass land denial, chaining extra turns, total extra-turn cards, Game Changers, and early and late combos for this exact list and its commanders.</p>
      : check.status === "conflict" ? <p className="build-note">EDHPowerLevel requires Bracket {check.requiredBracket}, above this list’s calculated Bracket {check.scoreBracket}. The cards need adjustment; the displayed bracket continues to follow the power score.</p>
        : check.status === "stale" ? <p className="build-note">This complete observation is older than seven days. Recheck the current list before relying on its restrictions.</p>
          : ["failed", "blocked"].includes(check.status) ? <p className="build-note">The site check did not complete, so restrictions remain unverified.{check.reason ? ` ${check.reason}` : ""}</p>
            : <p className="build-note">A complete EDHPowerLevel restriction analysis has not been recorded for this exact list and its commanders. Local checks and a numeric power score do not establish external restriction verification.</p>}
    {check.restrictions ? <details className="build-evidence"><summary>Observed EDHPowerLevel restrictions</summary>
      <ul>{Object.entries(RESTRICTION_LABELS).map(([key, label]) => <li key={key}><strong>{label}:</strong> {check.restrictions[key].length
        ? check.restrictions[key].map((entry, index) => <span key={index}>{index ? "; " : ""}{Array.isArray(entry)
          ? entry.map((name, cardIndex) => <span key={name}>{cardIndex ? " + " : ""}<DeckCardName name={name} /></span>)
          : <DeckCardName name={entry} />}</span>) : "None reported"}</li>)}</ul>
    </details> : null}
  </div>;
}

function GeneratedPackageDeck({ deck, packageNumber }) {
  const [expanded, setExpanded] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  if (!deck || deck.status !== "generated") {
    return <div className="build-generated-unavailable"><strong>Generated deck unavailable</strong><p className="muted">{deck?.reason || "This report predates generated package decks. A refreshed analysis can add one when enough verified card data is available."}</p></div>;
  }
  const groups = getGeneratedDeckGroups(deck);
  const stats = deck.statistics || {};
  const validation = deck.validation || {};
  const bracket = validation.bracket || {};
  const externalRating = getExternalDeckRating(validation.external_rating, undefined, validation.deck_sha256);
  const powerRating = getGeneratedDeckPowerRating(deck);
  const powerAlignment = getGeneratedDeckPowerAlignment(deck);
  const metadata = validation.metadata || {};
  const metadataFresh = isBuildMetadataFresh(metadata);
  const highestLift = stats.highest_average_lift;
  const exportText = getGeneratedDeckExport(deck);
  const primer = typeof deck.primer === "string" ? deck.primer : "";
  const distinctiveCards = getGeneratedDeckDistinctiveCards(deck);
  const bracketRepair = getGeneratedDeckBracketRepair(deck);
  const colorChoices = getGeneratedDeckColorChoices(deck);
  async function copyText(text, label) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      setCopyStatus(`${label} copied.`);
    } catch {
      setCopyStatus(`Clipboard unavailable. Download the ${label.toLowerCase()} or select its text below.`);
    }
  }
  return <details className="build-generated-deck" onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary><span>Generated {formatNumber(deck.card_count)}-card deck</span><span className="build-generated-deck__summary-note">{distinctiveCards ? `Distinctive cards: ${formatNumber(distinctiveCards.includedCount)}/${formatNumber(distinctiveCards.total)} included · ` : ""}Cards, export & primer</span></summary>
    {expanded ? <div className="build-generated-deck__content">
      <h5>{deck.name || `Package ${packageNumber} deck`}</h5>
      <p className="build-note">A suggested deck assembled from the sampled card pool for this package. Card names support hover and keyboard previews.</p>
      {colorChoices.map((choice) => <p className="build-note" key={choice.commanderName}>Chosen color for <DeckCardName name={choice.commanderName} />: <strong>{choice.colorName}</strong>. Reveal this choice before the game begins.</p>)}
      <div className="build-generated-deck__metrics">
        <span><strong>{formatNumber(deck.card_count)}</strong> cards including commanders</span>
        {Number.isFinite(stats.land_count) ? <span><strong>{formatNumber(stats.land_count)}</strong> lands</span> : null}
        {Number.isFinite(stats.swaps) ? <span><strong>{formatNumber(stats.swaps)}</strong> consensus swaps</span> : null}
        {Array.isArray(stats.land_adjustments) && stats.land_adjustments.length ? <span><strong>{formatNumber(stats.land_adjustments.length)}</strong> land adjustments</span> : null}
        {powerRating
          ? <span><strong>{powerRating.scoreBracket}</strong> {powerRating.kind === "local" ? "estimated" : "calculated"} power bracket</span>
          : <span>Power bracket unverified</span>}
      </div>
      {!metadataFresh ? <p className="build-generated-deck__review-note">Current card-rule freshness has not been verified. Refresh card metadata before relying on current legality.</p> : null}
      {powerAlignment ? <p className="build-generated-deck__review-note"><strong>Power calculated; deck review pending.</strong> This saved list is awaiting the deck-generation review.{powerAlignment.status === "known_mismatch"
        ? ` Checked card restrictions currently require Bracket ${powerAlignment.minimumCheckedBracket}, above the score’s Bracket ${powerAlignment.scoreBracket}. The cards still need adjustment.` : ""}</p> : null}
      <div className="build-generated-deck__actions">
        {exportText ? <><button type="button" onClick={() => copyText(exportText, "Deck")}>Copy for Moxfield</button><button type="button" onClick={() => downloadDeckText(exportText, deck.name, "txt")}>Download deck .txt</button></> : null}
        {primer ? <button type="button" onClick={() => copyText(primer, "Primer")}>Copy primer</button> : null}
      </div>
      {validation.external_url ? <p className="build-source-links"><SourceLink url={validation.external_url}>Check on EDHPowerLevel</SourceLink></p> : null}
      {bracketRepair ? <div className="build-generated-deck__review-note">
        <p><strong>Score-based bracket adjustment{bracketRepair.status === "blocked" ? " needs review" : ""}.</strong>{bracketRepair.previousPower !== null
          ? ` The original list scored ${bracketRepair.previousPower}, corresponding to Bracket ${bracketRepair.initialBracket}.`
          : ` The original score corresponds to Bracket ${bracketRepair.initialBracket}.`}{bracketRepair.targetBracket !== bracketRepair.initialBracket
          ? ` Recalculation during the adjustment changed the target to Bracket ${bracketRepair.targetBracket}.` : ""}</p>
        {bracketRepair.status !== "blocked" ? <>
          <p>Cards that raised the bracket were replaced. {bracketRepair.status === "retained"
            ? "These replacements were retained from an earlier repair; the original score is historical. " : ""}{externalRating?.status === "verified"
            ? "The updated list’s own EDHPowerLevel result appears below."
            : "The updated list needs its own current EDHPowerLevel check."}</p>
          <ul>{bracketRepair.swaps.map((swap, index) => <li key={index}><DeckCardName name={swap.out} /> → <DeckCardName name={swap.in} /></li>)}</ul>
        </> : <p>{bracketRepair.reason || "The available sampled cards could not resolve every bracket restriction while preserving deck construction requirements."}</p>}
      </div> : null}
      {powerRating?.kind === "local" ? <div className="build-external-rating" data-status="calculated">
        <p><strong>Local power estimate</strong><span>Inputs checked {checkedDate(powerRating.checked_at)}</span></p>
        <div className="build-generated-deck__metrics"><span><strong>{String(powerRating.power_level)}</strong> estimated power</span><span><strong>{powerRating.scoreBracket}</strong> estimated power bracket</span></div>
        <p className="build-note">Calculated for this exact card list using the public power formula and available card metadata. A current EDHPowerLevel result takes priority when available.</p>
        {powerRating.source_url ? <p className="build-source-links"><SourceLink url={powerRating.source_url}>Power formula source</SourceLink></p> : null}
      </div> : null}
      <ExternalDeckRating rating={validation.external_rating} deckHash={validation.deck_sha256} />
      <OfficialRestrictionCheck deck={deck} />
      <p className="build-generated-deck__copy-status" role="status" aria-live="polite">{copyStatus}</p>
      <p className="build-note">Paste the card list into Moxfield’s text import. Commander markers are included.</p>
      <div className="build-generated-deck__card-list" tabIndex="0" role="region" aria-label={`Package ${packageNumber} generated deck cards`}>
        {groups.map((group) => <section className="build-generated-deck__role" key={group.name}>
          <h6>{group.name} <span>{formatNumber(group.count)}</span></h6>
          <table><thead><tr><th scope="col">Card</th><th scope="col">Avg. inclusion</th><th scope="col">Avg. lift</th></tr></thead>
            <tbody>{group.cards.map((card) => <tr key={card.name}>
              <th scope="row"><span className="build-generated-deck__quantity">{card.quantity}</span> <DeckCardName name={card.name} /></th>
              <td>{formatBuildShare(card.average_inclusion)}</td><td>{formatBuildLift(card.average_lift)}</td>
            </tr>)}</tbody>
          </table>
        </section>)}
      </div>
      {stats.metric_note ? <p className="build-note">{stats.metric_note}</p> : null}
      <details className="build-evidence build-generated-deck__validation"><summary>Deck checks and selection</summary>
        <p className="muted">{validation.valid === true ? "The generated deck passed the structure checks recorded at generation." : "Review the recorded checks before using this deck."}</p>
        {validation.checks ? <ul className="build-generated-deck__checks">{Object.entries(validation.checks).map(([key, recorded]) => {
          const passed = recorded === true && (key !== "metadata_fresh" || metadataFresh);
          return <li key={key} data-passed={passed}><span>{passed ? "✓" : "—"}</span> {VALIDATION_LABELS[key] || key.replaceAll("_", " ")}</li>;
        })}</ul> : null}
        {metadata.checked_at || metadata.oldest_checked_at ? <p className="build-note">Rules-cache check recorded {checkedDate(metadata.checked_at)}. Oldest card record: {checkedDate(metadata.oldest_checked_at)}.{metadata.stale_cached ? ` ${formatNumber(metadata.stale_cached)} cached ${metadata.stale_cached === 1 ? "entry needed" : "entries needed"} a freshness check at generation.` : ""}</p> : null}
        {(validation.errors || []).length ? <ul>{validation.errors.map((error, index) => <li key={index}>{error}</li>)}</ul> : null}
        {Number.isFinite(bracket.recommended) ? <>
          <p className="build-note"><strong>Card-constraint guidance: bracket {bracket.recommended}{Number.isFinite(bracket.target) ? ` · construction target ${bracket.target}` : ""}.</strong> Based on {bracket.source === "membership_weighted_median" ? "brackets reported for the package’s sampled decks" : "the available sample"} and recorded card constraints. This guidance does not set the displayed power bracket.</p>
          {Number.isFinite(bracket.game_changer_count) ? <p className="build-note">{formatNumber(bracket.game_changer_count)} Game Changers{Number.isFinite(bracket.game_changer_limit) ? ` · bracket limit ${bracket.game_changer_limit}` : ""}.</p> : null}
          {(bracket.notes || []).map((note, index) => <p className="build-note" key={index}>{note}</p>)}
        </> : bracket.reason ? <p className="build-note">{bracket.reason}</p> : null}
        {stats.selection_reason ? <p className="build-note"><strong>Candidate selection:</strong> {stats.selection_reason}</p> : null}
        {stats.method ? <p className="build-note">{stats.method}</p> : null}
        {Number.isFinite(highestLift?.lift) ? <p className="build-note">Highest card average lift: <DeckCardName name={highestLift.name} /> · {formatBuildLift(highestLift.lift)}.</p>
          : Number.isFinite(highestLift) ? <p className="build-note">Highest card average lift: {formatBuildLift(highestLift)}.</p> : null}
        {stats.profile_names?.length ? <p className="build-note">Metric profiles: {stats.profile_names.join(", ")}.</p> : null}
        {stats.anchor_source_url ? <p className="build-source-links"><SourceLink url={stats.anchor_source_url}>Sampled starting deck</SourceLink></p> : null}
      </details>
      {primer ? <details className="build-evidence build-generated-deck__primer" open><summary>Package primer</summary><div className="build-generated-deck__primer-text">{splitPrimerCardReferences(primer).map((part, index) => part.type === "card"
        ? <span key={index}>[[<DeckCardName name={part.text} />]]</span> : <span key={index}>{part.text}</span>)}</div><button type="button" onClick={() => downloadDeckText(primer, `${deck.name || "generated-deck"}-primer`, "md")}>Download primer .md</button></details> : null}
      {exportText ? <details className="build-evidence"><summary>Import text</summary><textarea className="build-generated-deck__export" aria-label={`Package ${packageNumber} import text`} readOnly value={exportText} spellCheck={false} /></details> : null}
    </div> : null}
  </details>;
}

function PackageCard({ pkg, index, decks }) {
  const themes = getSupportedThemes(pkg);
  const bracketLabel = getPackageBracketLabel(pkg.generated_deck);
  const distinctiveCards = getGeneratedDeckDistinctiveCards(pkg.generated_deck);
  const restrictionCheck = getExternalDeckRestrictionCheck(pkg.generated_deck);
  const representatives = (pkg.representative_deck_ids || []).map(
    (id) => decks.find((deck) => deck.id === id)
  ).filter(Boolean);
  return (
    <article className="build-package">
      <div className="build-package__header">
        <div className="build-package__title"><h4>Package {index + 1}</h4>{bracketLabel ? <span className="build-package__bracket">{bracketLabel}</span> : null}</div>
        <span>{formatNumber(pkg.member_count)} sampled decks</span>
      </div>
      {restrictionCheck ? <p className="build-package__restriction-status" data-status={restrictionCheck.status}>{restrictionCheck.label}</p> : null}
      <p className="build-small-label">Suggested themes</p>
      <div className="build-theme-list">
        {themes.length ? themes.map((theme) => {
          const evidence = getBuildThemeEvidence(theme);
          return <span className="build-theme" key={theme.tag_slug || theme.tag_name}
            data-support-tier={evidence.tier || undefined} title={evidence.tooltip}>
            {theme.tag_name || theme.tag_slug}
            {evidence.note ? <small> · {evidence.note}</small> : null}
          </span>;
        }) : <span className="muted">No supported theme label</span>}
      </div>
      <GeneratedPackageDeck key={pkg.generated_deck?.name || pkg.id} deck={pkg.generated_deck} packageNumber={index + 1} />
      <details className="build-evidence">
        <summary>Distinctive cards{distinctiveCards?.omitted.length ? <span className="build-distinctive-exception-count"> · {formatNumber(distinctiveCards.omitted.length)} omitted from generated deck</span> : null}</summary>
        <div className="build-table-scroll" tabIndex="0" role="region" aria-label={`Package ${index + 1} card evidence`}>
          <table>
            <thead><tr><th scope="col">Card</th><th scope="col">In package decks</th><th scope="col">Other decks</th></tr></thead>
            <tbody>{(pkg.top_cards || []).map((card) => (
              <tr key={card.name}><th scope="row"><DeckCardName name={card.name} /></th><td>{formatBuildShare(card.member_rate)}</td><td>{formatBuildShare(card.other_rate)}</td></tr>
            ))}</tbody>
          </table>
        </div>
        {distinctiveCards?.omitted.length ? <div className="build-distinctive-exceptions">
          <p><strong>Not included in the generated deck</strong></p>
          <ul>{distinctiveCards.omitted.map((card) => <li key={card.name}><DeckCardName name={card.name} /><span>{card.reason}</span></li>)}</ul>
        </div> : null}
      </details>
      {representatives.length ? <p className="build-source-links">
        Examples: {representatives.map((deck, deckIndex) => (
          <SourceLink key={deck.id} url={deck.source_url || deck.preview_url}>deck {deckIndex + 1}</SourceLink>
        ))}
      </p> : null}
    </article>
  );
}

function DeckMixtures({ model }) {
  const [selectedPackage, setSelectedPackage] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const threshold = model.membership_threshold ?? 0.2;
  const rows = getDeckMixtureRows(model, selectedPackage, threshold);
  const visibleRows = showAll ? rows : rows.slice(0, 15);
  return (
    <details className="build-evidence build-deck-mixtures">
      <summary>Inspect individual decks and their package mixtures</summary>
      <p className="muted">Weights show each deck’s relative mixture of learned card packages. A deck can combine any number of packages. Reported source tags are shown separately and do not train the model.</p>
      <label className="build-filter">
        <span>Decks to show</span>
        <select value={selectedPackage} onChange={(event) => { setSelectedPackage(event.target.value); setShowAll(false); }}>
          <option value="all">All sampled decks</option>
          {model.packages.map((pkg, index) => <option key={pkg.id ?? index} value={index}>Package {index + 1} members (≥ {formatBuildShare(threshold)})</option>)}
        </select>
      </label>
      <div className="build-table-scroll" tabIndex="0" role="region" aria-label="Individual deck package weights">
        <table>
          <caption>{formatNumber(rows.length)} sampled decks{showAll || rows.length <= 15 ? "" : "; showing the first 15"}</caption>
          <thead><tr><th scope="col">Deck sources</th>{model.packages.map((pkg, index) => <th scope="col" key={pkg.id ?? index}>Package {index + 1}</th>)}<th scope="col">Reported tags</th></tr></thead>
          <tbody>{visibleRows.map((deck) => <tr key={deck.id}>
            <th scope="row"><div className="build-source-links"><SourceLink url={deck.source_url}>{deck.source_url?.includes("archidekt") ? "Archidekt" : deck.source_url?.includes("moxfield") ? "Moxfield" : "Original deck"}</SourceLink><SourceLink url={deck.preview_url}>EDHREC preview</SourceLink></div></th>
            {model.packages.map((pkg, index) => <td key={pkg.id ?? index}><span className="build-weight"><span className="build-weight__bar" style={{ width: formatBuildShare(Math.min(1, Math.max(0, deck.memberships?.[index] || 0))) }} /> <span>{formatBuildShare(deck.memberships?.[index])}</span></span></td>)}
            <td>{deck.source_tags?.length ? deck.source_tags.join(", ") : <span className="muted">None reported</span>}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {rows.length > 15 ? <button type="button" className="build-show-more" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer decks" : `Show all ${formatNumber(rows.length)} decks`}</button> : null}
    </details>
  );
}

function DeckPackages({ analysis }) {
  const [selectedCount, setSelectedCount] = useState("auto");
  const model = getBuildModel(analysis, selectedCount);
  const options = getBuildModelOptions(analysis);
  const selection = analysis.model_selection;
  const packages = model.packages || [];
  return (
    <div className="build-view">
      <div className="build-view__header">
        <div><h3>Recurring card packages</h3><p className="muted">Learned from cardlists, including decks with no tags. Each package can suggest one, several, or no theme labels.</p></div>
        {options.length ? <label className="build-filter build-model-select"><span>Number of packages</span><select value={selectedCount} onChange={(event) => setSelectedCount(event.target.value)}>
          <option value="auto">Auto — {analysis.package_count ?? analysis.packages.length} suggested</option>
          {options.map((count) => <option key={count} value={count}>{count} {count === 1 ? "package" : "packages"}</option>)}
        </select></label> : null}
      </div>
      <p className="build-note">A package is a group of cards that tend to appear together. Auto suggests a supported, simpler count. Each numbered choice fits a separate model: changing the count can regroup cards and change the theme labels. Package numbers do not identify the same strategy across models. More packages give finer detail, not necessarily a better explanation.</p>
      <div className="build-selection-note">
        <strong>{selection?.mode === "automatic" ? `Auto suggests ${analysis.package_count ?? analysis.packages.length} ${(analysis.package_count ?? analysis.packages.length) === 1 ? "package" : "packages"}` : `${packages.length} ${packages.length === 1 ? "package" : "packages"} in this analysis`}</strong>
        {selection?.reason ? <span>{selection.reason}</span> : <span>The package count depends on the available deck data.</span>}
        {selectedCount !== "auto" ? <span>Viewing {packages.length} {packages.length === 1 ? "package" : "packages"} as an exploratory alternative.</span> : null}
        {selection?.max_components ? <span>Tested counts from 1 to {selection.max_components}. Only supported alternatives are selectable; more deck data can support a different count.</span> : null}
      </div>
      {packages.length ? <>
        <div className="build-metrics">
          <div><strong>{formatNumber(packages.length)}</strong><span>card packages</span></div>
          <div><strong>{formatNumber(model.unique_decks)}</strong><span>unique sampled decks</span></div>
          <div><strong>{formatBuildShare(model.weighted_reconstruction_captured)}</strong><span>weighted card signal captured</span></div>
        </div>
        <p className="build-note">Package membership can overlap, so counts need not add up to the sample size. Distinctive-card percentages describe sampled decks. Tag percentages use the commander’s total EDHREC decks; tags can overlap. Main and supporting tiers describe reported tag prevalence with minimum sample sizes. Captured card signal measures model fit, not confidence or the share of decks explained.</p>
        <div className="build-package-grid">{packages.map((pkg, index) => <PackageCard key={pkg.id ?? index} pkg={pkg} index={index} decks={model.decks || []} />)}</div>
        <DeckMixtures key={selectedCount} model={model} />
      </> : <p className="muted">{analysis.reason || analysis.interpretation || "There are not enough usable decklists to discover card packages yet. Tag-profile comparisons are available in the other view."}</p>}
      {selection?.candidates?.length ? <details className="build-evidence"><summary>How the package count was chosen</summary><p className="muted">The model balances reconstruction on held-out decks, simplicity, support, and stability. This is an exploratory estimate of useful packages.</p>{selection.validation_note ? <p className="muted">{selection.validation_note}</p> : null}<div className="build-table-scroll" tabIndex="0" role="region" aria-label="Package count comparison"><table><thead><tr><th scope="col">Packages</th><th scope="col">Held-out error ↓</th><th scope="col">Result</th></tr></thead><tbody>{selection.candidates.map((candidate) => <tr key={candidate.components}><th scope="row">{candidate.components}</th><td>{formatDecimal(candidate.validation_error, 3)}</td><td>{candidate.selected ? "Suggested" : candidate.eligible ? "Supported alternative" : candidate.reason || "Insufficient support"}</td></tr>)}</tbody></table></div></details> : null}
    </div>
  );
}

function TagProfiles({ profiles }) {
  const pairs = getComparableProfilePairs(profiles);
  const [selectedPair, setSelectedPair] = useState("");
  const pairKey = (pair) => `${pair.left}|${pair.right}`;
  const pair = pairs.find((item) => pairKey(item) === selectedPair) || pairs[0];
  const names = new Map(profiles.profiles.map((profile) => [profile.tag_slug, profile.tag_name]));
  return <div className="build-view">
    <h3>Related tag profiles</h3>
    <p className="muted">Compare cards used more often in each tagged build than in this commander’s overall decks. Similar profiles suggest related card packages; they do not prove the same decks carry both tags.</p>
    <div className="build-profile-groups">{(profiles.groups || []).map((group, index) => <div key={index}><span className="build-small-label">Profile group {index + 1}</span><div className="build-theme-list">{group.map((slug) => <span className="build-theme" key={slug}>{names.get(slug) || slug}</span>)}</div></div>)}</div>
    <p className="build-note">Groups may share tags. A tag can also stand alone. Similarity threshold: {formatDecimal(profiles.threshold)}.</p>
    {pair ? <>
      <label className="build-filter"><span>Inspect a profile comparison</span><select value={pairKey(pair)} onChange={(event) => setSelectedPair(event.target.value)}>{pairs.map((item) => <option value={pairKey(item)} key={pairKey(item)}>{names.get(item.left)} + {names.get(item.right)} · {formatDecimal(item.similarity)}</option>)}</select></label>
      <div className="build-comparison-summary"><strong>{formatDecimal(pair.similarity)} distinctive-card similarity</strong><span>{formatNumber(pair.shared_observed_cards)} comparable observed cards</span><span>Raw card similarity: {formatDecimal(pair.raw_similarity)}</span></div>
      <div className="build-table-scroll" tabIndex="0" role="region" aria-label="Cards supporting the profile comparison"><table><caption>Shared distinctive cards · percentage points above commander baseline</caption><thead><tr><th scope="col">Card</th><th scope="col">{names.get(pair.left)}</th><th scope="col">{names.get(pair.right)}</th></tr></thead><tbody>{(pair.shared_signature_cards || []).map((card) => <tr key={card.name}><th scope="row">{card.name}</th><td>{formatBuildPoints(card.left_excess)}</td><td>{formatBuildPoints(card.right_excess)}</td></tr>)}</tbody></table>{!pair.shared_signature_cards?.length ? <p className="muted">No shared signature cards reported.</p> : null}</div>
    </> : <p className="muted">There are not enough comparable profiles to calculate a tag pair yet.</p>}
    <details className="build-evidence"><summary>Inspect all {profiles.profiles.length} tag signatures</summary><div className="build-profile-grid">{profiles.profiles.map((profile) => <article key={profile.tag_slug}><h4>{profile.tag_name}</h4><p className="muted">{formatNumber(profile.deck_count)} tagged decks · {formatNumber(profile.comparable_cards)} comparable cards</p><ol>{(profile.signature_cards || []).map((card) => <li key={card.name}><span>{card.name}</span><span>{formatBuildPoints(card.excess)}</span></li>)}</ol></article>)}</div></details>
  </div>;
}

function BuildReport({ report }) {
  const [view, setView] = useState(report.deck_analysis.packages.length ? "decks" : "profiles");
  const analyzed = report.deck_analysis.unique_decks || 0;
  const freshness = getBuildSourceFreshness(report.collection);
  const progress = getBuildCollectionProgress(report.collection);
  return <section className="commander-build-styles panel" aria-labelledby="build-discovery-heading">
    <div className="build-heading"><div><p className="eyebrow">Build discovery <span className="build-pilot">Exploratory</span></p><h2 id="build-discovery-heading">How this commander gets built</h2></div>{report.generated_at ? <span className="muted">Analysis built {new Date(report.generated_at).toLocaleDateString("en-US", { timeZone: "America/Toronto" })}</span> : null}</div>
    <p className="build-intro">Themes can overlap or describe different builds. Explore related tag profiles and the recurring card packages found in individual decks.</p>
    <p className="build-coverage">Coverage: {formatNumber(report.profiles.profiles.length)} tag profiles and {formatNumber(analyzed)} unique sampled decks from {formatNumber(report.total_edhrec_decks)} decks listed by EDHREC. This sample is not every deck and does not establish how common a style is.</p>
    <div className="build-source-status">
      <strong>{progress.targetMet ? "Full index verified recently" : progress.label}</strong>
      {!progress.analysisCurrent ? <p>Analysis awaiting the current deck collection. The packages shown still describe the previously analyzed decklists.</p> : null}
      {progress.known ? <p>{formatNumber(progress.attempted)} of {formatNumber(progress.frame)} available deck previews attempted. {progress.complete ? "Unavailable or unusable lists can still be excluded from analysis." : "Collection resumes across runs. A completed analysis of the collected decks does not mean the full index has been checked."}</p> : <p>The available index size and collection progress were not recorded for this report.</p>}
      {progress.unavailable > 0 ? <p>{formatNumber(progress.unavailable)} previews were unavailable or could not be read. Full deck coverage remains incomplete.</p> : null}
      <p className={`build-source-status__age build-source-status__age--${freshness.status}`}>{freshness.label}.{freshness.versionsVerified
        ? (freshness.oldestVerifiedAt ? ` Oldest version verification: ${checkedDate(freshness.oldestVerifiedAt)}.` : " Version-verification dates are unavailable.")
        : (freshness.oldestSourceAt ? ` Oldest successful preview check: ${checkedDate(freshness.oldestSourceAt)}.` : " This report has no verified source-check dates.")}</p>
      {freshness.versionsVerified ? <p className="build-note">{freshness.oldestSourceAt ? `Oldest actual decklist download: ${checkedDate(freshness.oldestSourceAt)}.` : "Original decklist-download dates are unavailable."} Current index checks verify unchanged deck IDs; new or changed versions require downloading the decklist.</p> : null}
      <p className="build-note">{progress.indexSourceAt ? `Deck index checked ${checkedDate(progress.indexSourceAt)}.${progress.indexFresh ? "" : " The index needs a freshness check."}` : "The deck index’s source-check date is unknown."}</p>
      <p className="build-note">Collection follows the request rate learned from EDHREC. Build discovery shows the catalog’s completion estimate. Check dates record when EDHREC was read; they do not establish when a deck owner last changed a deck. Rebuilding this analysis does not reset source age.</p>
    </div>
    <div className="build-view-switch" role="group" aria-label="Build discovery views"><button type="button" aria-pressed={view === "decks"} onClick={() => setView("decks")}>Individual deck packages</button><button type="button" aria-pressed={view === "profiles"} onClick={() => setView("profiles")}>Tag profile similarities</button></div>
    {view === "decks" ? <DeckPackages analysis={report.deck_analysis} /> : <TagProfiles profiles={report.profiles} />}
    <details className="build-evidence build-method-note"><summary>Data and interpretation</summary><p>{report.collection?.sampling || "Card profiles and deck previews collected from EDHREC."}</p><p>Deck tags are optional metadata. The package model uses cards, so untagged decks and decks with one or many tags can all contribute. Suggested themes describe a package’s similarity to tag profiles; they are not confirmed labels on every member deck.</p><ul>{(report.limits || []).map((limit) => <li key={limit}>{limit}</li>)}</ul></details>
  </section>;
}

export default function CommanderBuildStyles({ commanderSlug }) {
  const [state, setState] = useState({ loading: true, report: null, error: null });
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    loadCommanderBuildStyles(commanderSlug, { signal: controller.signal }).then(
      (report) => { if (active) setState({ loading: false, report, error: null }); },
      (error) => { if (active && error.name !== "AbortError") setState({ loading: false, report: null, error: error.message }); }
    );
    return () => { active = false; controller.abort(); };
  }, [commanderSlug]);

  if (state.loading) return <p className="muted" role="status">Loading build discovery…</p>;
  if (state.error) return <section className="commander-build-styles panel"><h2>Build discovery</h2><p className="muted">The build discovery report could not load. The commander’s tag table is still available.</p><details><summary>Report details</summary><p>{state.error}</p></details></section>;
  if (!state.report) return <section className="commander-build-styles panel"><h2>Build discovery</h2><p className="muted">No build discovery report has been collected for this commander yet. Reports use tag-specific card profiles and a sample of individual decklists.</p></section>;
  if (!hasBuildStylePopulation(state.report)) return <section className="commander-build-styles panel"><h2>Build discovery</h2><p className="muted">Build discovery requires at least 200 total EDHREC decks for this commander. This report does not have an eligible deck population.</p></section>;
  return <BuildReport report={state.report} />;
}
