# Commander build discovery

Build discovery is integrated into commander detail pages. It compares tag-specific card profiles and learns overlapping card packages from individual decklists. It is separate from specialization z-scores and does not require deck tags.

## Website behavior

Each sampled commander has two views:

- **Individual deck packages:** package cards, all supported suggested themes, distinctive card rates, representative source lists and individual deck mixtures.
- **Tag profile similarities:** overlapping candidate groups, pairwise card evidence and each tag’s signature cards.

**Auto** chooses a package count starting at one. The selector also offers each supported precomputed alternative, so visitors can explore one or more packages without browser-side fitting or live scraping. The count is not fixed at three. Each package can suggest one, several, or no themes; a deck can combine several packages. Source tags are optional annotations and never determine packages.

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

Tracked reports in `data/build-styles/` provide real starting data for Ghave, Hapatra and Jasmine. Each uses up to 12 tag profiles and 60 randomly sampled deck previews. The number 60 is the configured sample size, not EDHREC’s retrieval limit.

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

The collector attempts at most `--sample-decks` previews (default 60, CLI guardrail 200), after sampling IDs before examining cardlists or tags. Profiles use the most numerous reported tags with at least 25 decks by default, without a z-score gate. Use `--tags`, `--min-tag-decks` and `--max-tags` to adjust the profile cohort.

## Data access and quality

The collector uses public EDHREC routes:

```text
https://json.edhrec.com/pages/commanders/<slug>.json
https://json.edhrec.com/pages/commanders/<slug>/<tag>.json
https://json.edhrec.com/pages/decks/<slug>.json
https://edhrec.com/deckpreview/<urlhash>
```

The deck index lists `urlhash` IDs. Public previews embed card names, quantities, commanders and the original deck URL in `props.pageProps.data` inside `__NEXT_DATA__`. These formats were verified live. The collector does not request the original Moxfield/Archidekt deck or depend on those sites’ tags or API access.

Requests are sequential and cached, at least one second between starts. A 401, 403, 429 or unexpected challenge stops network collection. A blocked batch stops rather than trying another commander, and its persistent checkpoint pauses network collection for 24 hours. Other collection failures are recorded with a seven-day retry delay. This is an observed website format, not a guaranteed public API; schema failures are recorded.

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

Labels are suggested after fitting by comparing components to enriched tag profiles. All labels with cosine at least `max(0.20, 0.5 × best similarity)` are retained; there is no fixed tag-count cap. This similarity floor is a labeling heuristic, not calibrated tag confidence. An absent source tag is never treated as a negative label.

Weighted reconstruction capture is `1 - relative training error²`; it measures descriptive fit, not confidence or the share of decks explained. A few packages can summarize useful patterns while leaving most individual card choices unexplained.

## Static-site generation and deployment

Each primary package and supported alternative contains a generated 100-card
deck, including its commander or commander pair. Sampled lists are the card pool:
generation never adds an outside card name. A verified complete sampled deck
supplies the land base, functional roles and curve.
NMF memberships learn card preferences; deterministic swaps toward those
preferences preserve roles, types and curve buckets. Bounded candidate search
also compares other high-membership anchors and can replace up to three low-signal
nonessential spells with basics whose names already occur in the sample. A
verified below-target result can trigger up to three more land experiments,
capped at 40 lands and six total basic additions. Sampled duals and fetches can
upgrade the mana base using color demand and conditional untapped heuristics.
Price does not enter scoring.
Distinctive package cards already in the anchor are protected. This is
constrained construction from unsupervised evidence, not a separate text model.
Among reviewed candidates, a current external rating in the 72.5–77.5% target
takes precedence, then learned package consensus. When no candidate has a
qualifying external observation, selection uses native mana and role diagnostics
and keeps the external target explicitly unverified or unmet.

Scryfall supplies exact-name card rules, color identity and Commander legality
in cached batches. Missing verified metadata or a complete legal anchor produces
an unavailable result. Functional buckets use Oracle-text heuristics: Card
Advantage, Ramp, Removal, Wipe, Tutor, Recursion, Protection, Lands, Fast Mana
and Leftover. Cards can have several roles and supported package themes; labels
are reused in the native list and Moxfield export. Primers use `[[Full Card Name]]`
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

Export the lists, primers and per-card inclusion/lift CSVs locally:

```sh
.venv/bin/python scripts/export_package_decks.py \
  data/prototypes/build-styles/ghave/report.json
```

Per-commander reports are exported to `data/latest/build-styles/<slug>.json`. The availability index is `build-styles/index.json`, advertised by `site_manifest.json`. Reports preserve their own dates and are independent of the affinity snapshot date.

The exporter validates finite JSON and report identities, merges tracked seeds with previously successful and current processed reports, prefers newer successful samples, and filters commanders absent from the current dataset. Clean exports preserve those samples. The local site-data downloader follows the advertised availability index.

The catalog is the current commander index: 6,750 entries in the October 2026 local snapshot. Every indexed commander is eligible by default, with no minimum total-deck gate. The three tracked seed reports are starting data, not a coverage limit. Collection prioritizes unsampled commanders before samples older than 90 days. A commander with few usable previews may have profile evidence or an insufficient-data result; eligibility does not guarantee that machine learning can identify meaningful packages.

The **Update commander build styles** workflow continues the queue hourly at minute 17. It processes sequentially until its 45-minute time budget, checking the budget before starting another commander. The normal weekly data workflow also continues the same queue with a 15-minute budget. `--max-commanders 0` means no commander-count cap; a positive value is an optional per-run limit. A targeted manual workflow run can name specific indexed commanders.

The collectors share a concurrency group, preserve cumulative reports and `build-styles/_collection/state.json` between runs, and checkpoint after each commander. The checkpoint records successful attempts, insufficient-data results and retry delays. Source blocks impose a 24-hour pause across runs; other errors wait seven days before retrying. Each hourly artifact contains the entire cumulative directory, including the checkpoint, and is retained for seven days. Weekly artifacts remain available for 90 days. Restore accepts artifacts only from successful runs on the default branch; collector failures are reported without discarding completed work or the pause state.

A successful collection workflow triggers frontend deployment on main. These workflows must be deployed to the repository before scheduled collection can run. Full coverage takes sustained collection: at the defaults, one commander can require up to 79 EDHREC page requests including bracket profiles, plus shared batched card metadata, so thousands of commanders require many hours of paced requests. The website only displays evidence actually collected; it never invents a package report for an uncollected commander.

Inspect a batch without network collection:

```sh
.venv/bin/python scripts/build_site_build_styles.py --dry-run
```

Continue the complete pending catalog, stopping before starting another commander after 45 minutes:

```sh
.venv/bin/python scripts/build_site_build_styles.py --fetch \
  --max-commanders 0 --max-runtime-seconds 2700
```

Collect a targeted cohort:

```sh
.venv/bin/python scripts/build_site_build_styles.py --fetch \
  --commander-index frontend/public/data/latest/commanders/index.json \
  --commanders ghave-guru-of-spores hapatra-vizier-of-poisons \
  --max-commanders 2 --sample-decks 100 --max-components 10
```

Batch caches are dated. Interrupted, blocked and partial attempts resume their previous raw snapshot; successful reports are skipped until due for refresh. Partial deck generation retries after a day. CI uses `--prune-completed-cache` after durable report/checkpoint writes, retaining unfinished snapshots while keeping the catalog backfill from accumulating all raw HTML. Local caches remain unless that flag is supplied. The default 60 deck previews is adjustable up to 200, not an EDHREC limit.

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
