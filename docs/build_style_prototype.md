# Commander build discovery

Build discovery is integrated into commander detail pages. It compares tag-specific card profiles and learns overlapping card packages from individual decklists. It is separate from specialization z-scores and does not require deck tags.

## Website behavior

Each sampled commander has two views:

- **Individual deck packages:** package cards, all supported suggested themes, distinctive card rates, representative source lists and individual deck mixtures.
- **Tag profile similarities:** overlapping candidate groups, pairwise card evidence and each tag’s signature cards.

**Auto** chooses a package count starting at one. The selector also offers each supported precomputed alternative, so visitors can explore one or more packages without browser-side fitting or live scraping. The count is not fixed at three. Each package can suggest one, several, or no themes; a deck can combine several packages. Source tags are optional annotations and never determine packages.

The Build Discovery catalog defaults to most Auto-selected packages, with analyzed deck count breaking ties. Visitors can sort either direction by package count, analyzed decks, total EDHREC decks, tag-profile count, index coverage, analysis date or commander name. Inclusive count ranges, commander search, analysis status, coverage and source-check age filters combine across every current collector report before pagination. Filter state is preserved in the URL; unknown metrics remain distinct from zero and sort last. The catalog's existing 200-deck eligibility and fresh-start scope still apply before these display filters.

The package-tag filter matches a suggested tag on any package in the selected
automatic model. Options count distinct commanders across all current reports;
general commander profiles, source deck tags and alternative models do not
qualify a match. A selected tag combines with the other filters and stays in
the URL. Matching tags are highlighted while all packages for each matching
commander remain visible.

Discovery cards include commander thumbnails, name hover/focus previews and a
keyboard/touch disclosure for reading full card images. Partners and all card
faces remain separate and readable. The index includes compact summaries of
each selected package's suggested tags and generated-deck bracket. Current
external bracket observations retain their dated verification; native guidance
is labeled estimated. Missing labels and brackets remain explicitly unavailable.
These summaries use saved reports and catalog images, so publishing them does
not require recollecting decklists or fetching every full report in the browser.

Coverage, sample size and collection date are shown. The collection queue includes every commander in the current index, including commanders with few decks. Missing reports produce an explanatory unavailable state without interrupting the commander’s tag table. Low-data results are recorded instead of forcing packages. Reports are sampled evidence, not estimates of every build’s population share.

## Automatic package count: 1–N

The analyzer evaluates K from 1 through a data-supported maximum:

```text
maximum K = min(configured ceiling, usable decks // 4, feature count, matrix rank)
```

The default configured ceiling is eight and can be changed with `--max-components`. Each candidate uses four initializations and three seeded 80/20 deck splits. Candidate diagnostics include held-out reconstruction error, support and consistency.

The explicit conservative selection rule is:

1. Require each package to have at least three supporting decks and at least 5% of the sample, both as thresholded members and summed relative membership.
2. Require mean matched component cosine of at least 0.90 across initializations and 0.80 across the training subsets, plus converged fits.
3. Score held-out relative squared reconstruction error plus `(K - 1) / usable decks` as a simplicity penalty.
4. Choose the smallest eligible K within one standard error of the best eligible score.

This is an exploratory heuristic, not AIC, calibrated confidence or proof of the true number of strategies. Vocabulary and IDF are held fixed from the full sample during validation; this is reconstruction in a fixed feature space, not independent prediction of unseen cards. Very homogeneous samples can return one shared recurring-card package instead of forcing a split. Package fitting requires at least eight unique usable decks; samples without adequate evidence return an insufficient-data result. Tag-profile comparisons can still be available when deck packages cannot be fitted.

All eligible alternatives are stored in `deck_analysis.alternative_models`, with package summaries, overlap and ID-keyed memberships. Unsupported counts are shown in diagnostics but are not selectable on the website. Package numbering has no strategic meaning and can differ between alternative counts.

## Real launch samples

Tracked reports in `data/build-styles/` provide real starting data for Ghave, Hapatra and Jasmine. These initial snapshots use up to 12 tag profiles and 60 randomly sampled deck previews. That was the original configured sample size, not EDHREC’s retrieval limit; the collector now defaults to the full available index.

The Ghave sample contains 60 unique decks originating from 30 Moxfield and 30 Archidekt lists. Only six had tags reported by EDHREC, but all 60 could be analyzed from their cards. Automatic selection suggests two packages; supported alternative counts include one, three, four, five and seven. The two-package fit captures about 17% of the weighted card signal. Its broad themes include Combo/Aristocrats and Saprolings/Fungi, with multiple suggested labels per package.

These observations describe that dated sample. Budget, power bracket, deck age, precon lineage and near copies may explain some differences alongside strategy.

## Collect and analyze a commander locally

Use Python 3.12 or newer from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,prototype]"
.venv/bin/python -m edhrec_affinity.build_styles \
  --commander ghave-guru-of-spores --fetch \
  --components auto --max-components 8 \
  --output-dir data/prototypes/build-styles/ghave
```

This writes a self-contained `index.html` and `report.json`. The HTML can be opened directly without a server. Use `--no-html` for JSON-only production generation. An explicit `--components 1` or any feasible positive count requests a manual exploratory fit instead of automatic selection.

Omit `--fetch` for cache-only analysis. Existing pages are reused even with `--fetch`; choose a new cache directory for a fresh snapshot. Per-commander caches default to `data/raw/build-style-prototype/<slug>/`.

The collector defaults to `--sample-decks all`, with no preview-count ceiling. A nonnegative count remains available for experiments; `0` uses imports only. IDs are shuffled before examining cardlists or tags. Batch jobs collect for up to five minutes per commander before analyzing all lists collected so far, saving compressed parsed lists and a frozen index in `_collection/previews/`. Later jobs resume rather than starting over. Reports expose the index size, cumulative attempts, loaded lists and completion status. All available index IDs does not guarantee every source-site deck is accessible or usable. Profiles use the 12 most numerous reported tags satisfying the supporting threshold by default, without a z-score gate. A matched profile is a **main** tag when its reported tag decks are at least `max(15, ceil(0.03 × commander total decks))`; it is a **supporting** tag when it reaches `max(5, ceil(0.01 × commander total decks))` but falls below the main threshold. The denominator is the full total reported on the same commander baseline page, never the sampled, loaded or usable deck count. The 15/5 count floors prevent a handful of decks from receiving strong labels for a small commander; the 3%/1% shares prevent an increasingly popular commander from accumulating labels solely by crossing small fixed counts. For 500 total decks the thresholds are 15/5; for 10,000 they are 300/100. These tiers describe commander-wide prevalence with a minimum evidence floor, not package importance, calibrated confidence or the fraction of decks in a package. EDHREC tags overlap and their percentages need not sum to 100%. Use `--tags`, `--min-tag-decks`, `--main-tag-decks`, `--min-tag-fraction`, `--main-tag-fraction` and `--max-tags` to adjust the cohort. An omitted main count floor resolves to 15, or to a higher collection minimum for compatibility with older single-threshold commands. Missing or invalid commander totals cannot establish relative tag evidence; the model can still learn unlabeled card packages.

Collected evidence, reports and pipeline records retain their version-2 `tag_label_policy`, containing population fractions and evidence floors without commander-specific totals. Reports and evidence separately record `tag_label_thresholds` with the baseline denominator and effective count cutoffs. A policy change schedules existing reports for an upgrade before untouched commanders, using saved deck previews and the shared source request limiter to load missing tag profiles. This does not restart deck sampling or invent newer source observation dates. Legacy or corrupt pending inputs must return through collection before the new policy is certified.

A package is a recurring card pattern across collected decks, and one deck can mix several patterns. Each selectable count is fitted separately: package numbers do not identify the same strategy across models. Each package gets its own generated 100-card example. Generated card lists and Moxfield exports omit category tags; internal role checks can still recognize multiple functions for construction and primers.

Collections above 1,000 usable lists use sparse card matrices and a rank probe up to the model-count ceiling. All usable decks still enter model fitting. The optional quadratic near-copy diagnostic is omitted at that size (reported as unavailable); exact-copy filtering remains active. This avoids dense full-rank decompositions and pairwise comparisons growing with the uncapped index.

## Data access and quality

The collector uses public EDHREC routes:

```text
https://json.edhrec.com/pages/commanders/<slug>.json
https://json.edhrec.com/pages/commanders/<slug>/<tag>.json
https://json.edhrec.com/pages/decks/<slug>.json
https://edhrec.com/deckpreview/<urlhash>
```

The deck index lists `urlhash` IDs. Public previews provide card names, quantities, commanders and the original deck URL. Legacy previews use `props.pageProps.data` inside `__NEXT_DATA__`; current previews use a React stream at the same public URL. Both formats have been verified live. The collector does not request the original Moxfield/Archidekt deck or depend on those sites’ tags or API access.

The standalone CLI defaults to sequential cached requests, at least one second between starts. Production pipeline jobs instead use the shared adaptive limiter described below. A 401, 403, 429 or unexpected access challenge stops network collection. The pipeline preserves a cooldown of at least one hour and honors longer server Retry-After values; access restrictions require review before automatic restart. The older sequential batch mode retains its 24-hour block and seven-day error retries. These are observed website formats, rather than a guaranteed public API; schema failures are recorded separately.

Deck quality checks enforce commander identity and a rough 90–100 card completeness range, including commanders. Printings are merged by normalized name; exact card-count copies are deduplicated. Near copies are reported but retained. These checks are not a complete Commander legality validator.

## Profile method

Card inclusion uses each card’s `num_decks / potential_decks`. Land groups, dedicated new-card groups and observations with less than 90% of the page’s eligible-deck window are excluded.

```text
baseline = card inclusion in the commander’s unfiltered profile
regularized rate = (tag card count + 20 × baseline) / (tag eligible decks + 20)
enrichment = max(0, regularized rate - baseline)
```

Enrichments below one percentage point become zero. The 20-deck regularizer and one-point floor are explicit heuristics, unrelated to the fitted affinity model. Pairwise cosine similarity uses only cards observed on both tag pages and the baseline; omitted entries remain unknown. A comparison requires 20 shared measured cards and three enriched cards in each profile.

Groups are maximal cliques above a similarity threshold: every pair must qualify, and a tag can appear in several groups. Similarity does not measure deck intersections, strategic compatibility or actual combinations of tags on individual decks. Truncated baseline lists also limit rare-package discovery.

## Individual-deck method

The model excludes commanders, identifiable lands, cards appearing in fewer than two decks and staples appearing in at least 85% of usable decks. The shared-card fallback for homogeneous samples is explicitly reported. Features record card presence, weighted by `log((1 + decks) / (1 + card support))`, then each deck is L2-normalized.

Non-negative matrix factorization learns recurring card components. Component scales are normalized before producing relative package weights. These weights are not probabilities. Package membership counts use a default relative-weight threshold of 0.25 and can overlap.

Labels are suggested after fitting by comparing components to enriched tag profiles. Both main and supporting candidates still need at least 20 comparable cards, at least three enriched cards and cosine at least `max(0.20, 0.5 × best similarity)`. All qualifying labels from the collected cohort are retained; there is no extra display-count cap. Lowering the deck-count threshold does not force a label onto an unrelated component. Each suggestion retains its `support_tier`, reported `deck_count`, full `commander_deck_count` and unrounded `deck_fraction`. This similarity floor is a labeling heuristic, not calibrated tag confidence. An absent source tag is never treated as a negative label.

Weighted reconstruction capture is `1 - relative training error²`; it measures descriptive fit, not confidence or the share of decks explained. A few packages can summarize useful patterns while leaving most individual card choices unexplained.

## Static-site generation and deployment

The builder attempts a 100-card deck for each primary package and supported
alternative, including its commander or commander pair. Sampled lists are the card pool:
generation never adds an outside card name. A verified complete sampled deck
supplies the land base, functional roles and curve.
Displayed distinctive cards form a priority core. The builder first attempts to
include every verified, legal distinctive card from the sampled pool, trying
other observed anchors when needed. These substitutions preserve land slots,
essential ramp/draw/interaction floors, singleton identity and checked bracket
restrictions; role, type and curve similarity are preferences at this stage.
If constraints force a choice, higher learned distinctive weight takes priority.
Every displayed card is accounted for as included or omitted with a specific
reason. Included core cards remain protected during later optimization.
When verified commander text permits choosing a color before the game, the
builder evaluates the legal color choices for each package and records the
selected choice and effective deck identity. The deck view and primer explain
that choice. This supports Clara Oswald's additional color without changing
Scryfall's static card identity or admitting multiple unchosen colors.
NMF memberships then guide ordinary consensus swaps that preserve roles,
types and curve buckets. Bounded candidate search
also compares other high-membership anchors and can replace up to three low-signal
nonessential spells with basics whose names already occur in the sample. A
verified below-target result can trigger up to three more land experiments,
capped at 40 lands and six total basic additions. Sampled duals and fetches can
upgrade the mana base using color demand and conditional untapped heuristics.
Price does not enter scoring.
This is constrained construction from unsupervised evidence, not a separate
text model. Candidate selection prioritizes a successful score-based bracket
repair when a current exact-list observation requires one, then maximizes distinctive-card coverage and
learned core weight. Within that tier, a current external rating in the
72.5–77.5% target takes precedence, then learned package consensus.
When no candidate in that tier has a
qualifying external observation, selection uses native mana and role diagnostics
and keeps the external target explicitly unverified or unmet.

Generation version 3 adds score-based bracket repairs to the core coverage,
omission explanations and primer references recorded by version 2, for both the
selected model and alternative package counts. The
collector runs a bounded upgrade after collection for the same current reports
eligible for Build Discovery. Saved parsed lists and profiles rebuild older
generated decks without EDHREC requests, refitting NMF or changing source-check
dates. One paced Scryfall stream refreshes required card metadata before CPU
workers start; a missing input or stale rule for a selected card preserves the
previous report for retry. Successful upgrades request publication even if
collection itself produced no new reports. Originals are backed up, missing
required inputs leave existing reports intact, and remaining work resumes in
later batches. For a restored checkpoint, the same operation is available as:

```bash
python scripts/rebuild_generated_decks.py \
  --build-styles-dir data/processed/build-styles-current \
  --card-metadata-cache data/raw/build-style-card-metadata.json \
  --workers 2 --only-outdated --current-discovery-only --fetch-metadata
```

Omit `--fetch-metadata` for a fully offline rebuild using verified cached rules.
The runtime bound includes metadata preparation, and unresolved unused cards
do not prevent rebuilding a deck whose selected cards have current rules.

Scryfall supplies exact-name card rules, color identity and Commander legality
in cached batches. Unresolved names receive an exact named-card lookup. Names
that resolve to a token, Plane, art card or other non-deck object receive
an exact search excluding those layouts. Only a complete response with one
verified Oracle identity is accepted; ambiguous identities stay unresolved,
and normal banned cards retain their actual legality.
English alternate names from Scryfall's `flavor_name` and `printed_name` fields retain
their printing and Oracle identity evidence in the shared cache. EDHREC's
constituent commander printing IDs provide another fallback: the Scryfall
response must match that ID and explicitly confirm the requested alternate name.
Explicit EDHREC
alternate commander configurations can also resolve an individual card when
exactly one name differs; partner positions and card-page links alone do not
establish an alias. Ambiguous identities stay unresolved. Deck construction
uses the same verified identities to match sampled commanders and enforce
singleton rules, including when a sampled list uses the regular printing's name.
Missing verified metadata or a complete legal anchor produces
an unavailable result. Functional buckets use Oracle-text heuristics: Card
Advantage, Ramp, Removal, Wipe, Tutor, Recursion, Protection, Lands, Fast Mana
and Leftover. Cards can have several internal roles for construction and primers;
the native card list and Moxfield export do not display category tags. Primers use `[[Full Card Name]]`
references, with native hover previews and copy/download.

Per-card ranking uses matched package themes and the selected bracket page,
excluding the general commander page. Each page contributes equally; an unlisted
card contributes zero to the requested average. Lift is the raw EDHREC multiplier,
not the model's percentage-point enrichment. Coverage counts distinguish measured
entries from zero-filled missing entries. These deck-ranking conventions do not
change how missing data is handled by the statistical model.

Bracket guidance starts from the NMF-weighted median of sampled deck brackets,
defaulting explicitly to 3 when no annotations are available. It checks Game
Changer counts, detectable extra turns and mass land denial, raising the
recommendation when needed. This adapts the repository's bracket approach to
actual deck evidence; the commander z-score classifier does not establish a
generated deck's power. Combo speed, all play patterns and the 72.5–77.5%
external playability target are not certified by structure checks. Each list
links to the official [EDHPowerLevel preloaded deck flow](https://edhpowerlevel.com/button/).
Observed external ratings are stored under `data/build-styles/validations/`,
matched by a hash of all card names and quantities, and marked stale after seven
days. A changed card list cannot inherit a previous list's rating. The scheduled
collector does not claim to have run a live external check merely by generating
the list or its validation link.

Numeric power scores use a centralized policy in `power_brackets.py`: below
4.7 is Bracket 1; 4.7–<6.7 is Bracket 2; 6.7–<7.7 is Bracket 3;
7.7–<9.25 is Bracket 4; and 9.25 or above is Bracket 5. The repository owner
provided these EDHPowerLevel thresholds on October 27, 2026; the official
[public client script](https://edhpowerlevel.com/js/main-C1lbqCDd.js) was checked
that day and contains `bracketCurve:[0,4.7,6.7,7.7,9.25,10]`. This is separate
from the site's reported bracket and its playability percentage. For example,
6.09 maps to Bracket 2 even if a mass land denial card raises the reported
bracket to 4. Only a current exact-list observation with an unambiguous numeric
score may drive score-based deck repairs. Missing scores, ranges and labels
such as `10+` remain available as observations but do not supply an exact score.
An exact-list observation may also record `bracket_restrictions`: lists of full
card names under `game_changers`, `extra_turns` and `mass_land_denial`, and lists
of groups of at least two distinct card names under `early_combos` and
`late_combos`. All named cards must belong to the exact checked list. These are
observed results, not an inferred complete combo search.

When a verified numeric score maps below the deck's restriction-driven bracket,
the builder attempts replacements from the sampled package pool. It checks
Game Changer limits, extra turns, mass land denial and any recorded early/late
two-card combos. Mass land denial detection includes Restore Balance's
sacrifice-down-to-the-fewest-lands wording. A bounded search breaks prohibited
combos while preserving the commander, 100-card count, Oracle singleton and
color legality, land slots, colored sources and essential functional roles.
Distinctive cards remain priorities; an unavoidable omission explicitly says
which score/bracket restriction required it. If the restrictions cannot all be
resolved, the existing list is retained with a blocked explanation.

Each successful repair records the original exact card list and rating, swaps
and construction checks. The changed list receives only its own exact-list
external observation; the old score is never transferred. Subsequent rebuilds
reuse the original scored seed while that observation is current. Once it is
stale, the builder can retain the unchanged repaired list only after rechecking
its current pool, metadata, legality and bracket restrictions; it labels the repair
historical and does not make new score-driven swaps from stale evidence.

Export the lists, primers and per-card inclusion/lift CSVs locally:

```sh
.venv/bin/python scripts/export_package_decks.py \
  data/prototypes/build-styles/ghave/report.json
```

Per-commander reports are exported to `data/latest/build-styles/<slug>.json`. The availability index is `build-styles/index.json`, advertised by `site_manifest.json`. Reports preserve their own dates and are independent of the affinity snapshot date.

The exporter validates finite JSON and report identities, merges tracked seeds with previously successful and current processed reports, prefers newer successful samples, and filters commanders absent from the current dataset. Clean exports preserve those samples. The local site-data downloader follows the advertised availability index.

Build Discovery starts fresh under a minimum of 200 total EDHREC decks per commander. The 66 previously visible reports all fell below this minimum and are hidden. Collection, saved-evidence analysis, static export, discovery cards and individual commander package views enforce the same floor; missing population counts fail closed. The policy also requires collection after `2026-10-23T03:13:45Z`, a valid checkpoint, full index coverage and current analysis matching the collected dataset. Run 39893813350 (151 new previews and 25 reports) remains historical provenance rather than a display allowance. Later qualifying reports join automatically, including unchanged analyses freshly verified. Older evidence is retained. Search and displayed coverage counts use the same eligible visible set.

The October 2026 catalog contains 6,750 commanders; 3,374 currently have at least 200 total decks and qualify for collection. This population count is separate from the number of usable previews sampled. Every available deck-index ID for an eligible commander is targeted, without an eventual preview-count ceiling. A fresh source page that drops below 200 is skipped before preview collection and fitting, and its eligibility is checked again after the index cache interval. An eligible commander can still have insufficient usable evidence for packages.

The **Update commander build styles** workflow uses `--pipeline`: rolling 30-minute batches collect one commander at a time, with up to sixteen preview workers and two independent model processes. Saved partials and untouched commanders are ordered by estimated remaining work, so near-complete collections finish before broad catalog sweeps. Finished reports due for refresh follow unfinished work. A progressed slice returns according to the same priority; the five-minute safety slice does not send it behind the full catalog. Model fitting can run while collection starts for the next commander. All source workers share one adaptive request budget. The configured ceiling is 40 starts per second, initially ramping from two; it is not multiplied by the worker count or treated as EDHREC's actual limit. An HTTP 429 stops the batch immediately and records an operating ceiling 25% below the rate at that response. Later jobs start and ramp within the learned ceiling. Further throttling lowers it again; concurrent responses to the same block do not compound the reduction. Network errors reduce the current rate, while 401/403 stop collection without inferring a rate. Server Retry-After and the existing minimum cooldown remain enforced across jobs. The repository owner confirmed permission for automated EDHREC collection on October 21, 2026. No alternate route is used to evade access restrictions.

A shared transport discovers the site's public page format once. Legacy Next.js pages supply a build ID for compact JSON; current App Router pages supply a React stream at the same public preview URL with the `RSC: 1` rendering header. The parser reads JSON records without executing page scripts, and rediscovers the format after a deployment invalidates it. Access blocks never cause a fallback around the block. One current preview measured 301 KB as HTML and 42 KB as a React stream; this is an observation about that payload, not a promised network speedup. Unsupported data formats remain retryable and preserve prior usable cache entries; they do not permanently exclude decks or masquerade as rate limits.

Collection has no fixed completion deadline by default. Build Discovery and capacity summaries distinguish the configured ceiling, learned operating ceiling, completed HTTP requests, remaining work, fastest request-only collection time, measured projection, pending analysis and unavailable previews. The fastest estimate assumes the learned rate can be sustained and includes known cooldown time; latency, retries, model fitting and runner gaps can extend it. Until a source rate has been learned, the configured ceiling supplies only a provisional lower bound. Neither the first throttled response nor that provisional bound establishes an exact permanent source limit. A later weekly snapshot date starts a new collection cycle without resetting learned rates or source ages.

Source freshness remains independent of completion estimates. `--freshness-target-hours` controls the age used to classify verification checks; it does not create a completion deadline. Existing deck lists, report dates, source ages, retries, learned limits and cooldowns survive collector restarts.

The per-run 30-minute budget limits both collection and submission of new model work. Already running models have a 120-second grace period; unfinished CPU processes are then stopped and their saved inputs remain `analysis_pending`. Subsequent runs prioritize those inputs without downloading their decks again. EDHREC and Scryfall request streams stop starting new requests at the same deadline. A run-budget pause is not a source rate limit or a failed deck, and does not create a retry cooldown. Completed results remain publishable even when other analyses are deferred.

Deck regeneration also enforces its 20-minute budget on running workers, including a single worker and the serialized metadata prefetch. Terminable CPU workers prepare results; the parent process alone makes backups and atomically commits completed reports. Shell watchdogs and separate 36/22-minute step ceilings protect collection and regeneration, leaving 32 minutes of the 90-minute job for setup, restoration, checkpoint uploads and publication. Checkpoint uploads always run after work-stage failures and use reduced compression; a timeout of the entire job would still prevent later steps, so work must stop before that outer deadline.

EDHREC's FAQ explains that edited decks receive a new database entry and the old one is removed: https://edhrec.com/faq . The rolling collector reconciles added/removed IDs and changed row fingerprints, immediately downloads new/changed versions, and verifies unchanged versions against a recent index. Original deck download timestamps remain separate from index verification timestamps. Dataset/ranking/card-rule hashes skip model fitting only when its real inputs and model settings are unchanged. This is freshness relative to EDHREC; its own upstream ingestion delay remains outside this project's control.

Parsed lists, source hashes, small page caches and pending analysis inputs remain in `build-styles/_collection/` across jobs, including completed datasets needed for incremental refresh. Bulky per-preview responses are discarded only after their parsed data is durably saved. Model workers consume saved evidence and make no EDHREC or Scryfall requests. One shared Scryfall repository loads its cache once and coalesces concurrent card lookups; pipeline metadata has a three-day cache policy.

After a durable artifact upload, an unrestricted run self-dispatches its successor only while actionable work remains and progress was made. A separate ten-minute watchdog checks compact summary metadata and active runs before dispatching; idle checks do not restore the full dataset. A separate cooldown workflow requires an actual HTTP 429 in that batch, its durable checkpoint artifact and a verified `build-style-cooldown` environment wait timer of at least 60 minutes. The environment wait consumes no hosted-runner time ([GitHub wait timer documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments#wait-timer)). After waiting, it rechecks the latest cooldown and active collectors; longer Retry-After values repeat the protected wait with a bound of 48 attempts. Missing timer configuration stops automatic cooldown handling. Confirmed, saved rate-limit pauses finish successfully; access restrictions and other failures remain failures. The Monday workflow restores reports and triggers collection after publishing the new weekly dataset; it no longer competes to write the collection checkpoint. A failed collector marks its workflow failed after saving progress; updated partial reports can still deploy when the summary requests publication. After both checkpoint and summary uploads succeed, the collector explicitly dispatches frontend publication with that exact source run. This uses the supported GITHUB_TOKEN workflow_dispatch route; bot-created collectors do not rely on a downstream workflow_run event. The publisher validates the uploaded source and pins restoration to it, including the short interval while its producing workflow finishes. A watchdog repairs missing or failed publication without replacing newer published checkpoints. Frontend deployment is skipped when a batch changed no published reports. Restore validates cumulative artifacts and can recover usable checkpoints from completed interrupted runs on the default branch. Checkpoint artifacts are retained two days; summaries seven days. A failed upload prevents automatic continuation.

Full-dataset storage and runner capacity must support the workload. The collector pauses before consuming its configured free-disk reserve; it does not provision paid storage or claim GitHub artifact quotas can hold every future dataset. Source limits, insufficient storage, runner gaps, unavailable lists or slow analysis can extend completion and remain visible in progress summaries. A complete eligible-commander production backfill has not yet established sustained end-to-end throughput.

If an interrupted run saves its summary but fails to upload its cumulative checkpoint, automatic dispatch stops. An explicit **Run workflow** request can set `recovery_run_id` to that latest interrupted run. Recovery pins the newest older durable checkpoint and retains the stricter learned request ceiling and applicable cooldowns from both runs. Access restrictions and active cooldowns still prevent collection. The restore is checked again before updating state; unuploaded preview counts and completed reports are never copied. The eligible commander queue is rebuilt using the 200-deck minimum. Checkpoint uploads exclude raw preview responses, model scratch and temporary files that can be removed during cancellation, while retaining parsed decklists and analysis inputs.

An October 22, 2026 local recovery check read the previously failing Timin preview and 126 Ghave decks with no format errors, then stopped on HTTP 429 after about 65 seconds at 1.96 successful requests per second. This confirms format recovery, but does not establish the request rate available to GitHub runners.

Inspect the current catalog and request-only capacity without fetching data:

```sh
.venv/bin/python scripts/build_site_build_styles.py --pipeline --dry-run
```

Run the rolling full collector under the existing source-access permission:

```sh
.venv/bin/python scripts/build_site_build_styles.py --pipeline --fetch \
  --workers 1 --preview-workers 16 --analysis-workers 2 \
  --requests-per-second 40 --sample-decks all \
  --min-total-decks 200 --source-max-age-hours 72 \
  --index-max-age-hours 24 --refresh-after-days 3 \
  --max-runtime-seconds 1800 --prune-completed-cache
```

The Build Discovery page checks published results and the latest public GitHub workflow state every two minutes while visible. A newer running or queued collector overrides an older published pause label; source coverage and estimates remain explicitly dated to their published checkpoint. GitHub API failures leave the last usable snapshot in place, with rate-limit backoff. These checks report workflow execution, not live HTTP request counts.

`--max-commanders`, `--commanders`, and a numeric `--sample-decks` remain optional bounded experiments. A finite sample cannot satisfy full-index coverage, and bounded/manual refresh runs do not self-dispatch indefinitely. Without `--pipeline`, the older sequential batch mode remains available for local experiments; its dated cache/refresh behavior is separate from the production rolling pipeline.

## Local deck imports

A folder of `.txt` or `.json` exports can replace or supplement previews:

```sh
.venv/bin/python -m edhrec_affinity.build_styles \
  --commander ghave-guru-of-spores --sample-decks 0 \
  --deck-import data/prototypes/my-exports \
  --output-dir data/prototypes/build-styles/local-imports
```

The baseline/profiles must be cached, or use `--fetch`. One file represents one deck. Text accepts quantity/name lines, optional set/collector annotations and board headings; commander cards must appear. Sideboards and maybeboards are excluded. Text lacks types, so unrecognized nonbasic lands can remain.

JSON imports accept normalized card records plus captured EDHREC, Archidekt and Moxfield board shapes. The deckbuilder adapters are fixture-tested; their current live APIs are not dependencies. Normalized JSON uses `commanders: [name]`, optional `source_url` and `tags`, and `cards: [{name, quantity, type_line}]`. Source tags may be omitted or have any count and never affect fitting.

## Validation

```sh
.venv/bin/python -m pytest -q
cd frontend
npm test
npm run lint
npm run build
```

Tests cover missing observations, staple confounding, overlapping profile groups, denominators, land/board exclusions, exact duplicates and incomplete lists; recovery of one, two and four card packages, hybrid decks, rare unsupported components, label invariance and alternative memberships; plus report loading, arbitrary theme counts, optional data, clean exports, seeds, batching, access blocks and safe JSON embedding. Unit tests make no live website requests.
