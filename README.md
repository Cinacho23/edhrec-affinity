# EDHREC Commander Tag Affinity Analysis

A static data-analysis website for exploring which Magic: The Gathering Commander decks are unusually associated with EDHREC tags.

The project collects commander deck/tag data, computes tag-affinity metrics, tracks changes across snapshots, enriches commanders with card metadata, and publishes the results through a searchable React/Vite website.

## Research question

Which commanders are unusually associated with specific EDHREC tags after adjusting for commander popularity, tag-wide baseline behavior, and sample size?

This project is not trying to reproduce EDHREC. Its purpose is to add an original statistical layer on top of commander/tag counts.

## Main features

- Commander search
- Cumulative tag filters across commander list pages: every selected tag must have z > 1, with matching commanders sorted by their summed z-scores
- Commander detail pages with a minimum of 5 tagged decks per displayed tag
- Synergy Deck generation for the Commander-legal configurations in the commander index, with four budgets and exact Card Kingdom printings
- Build discovery with a resumable collection queue for every indexed commander: automatic 1–N card packages when supported, overlapping theme suggestions, tag-profile evidence and source decklists
- Global commander-tag leaderboard
- Tag explorer
- Set-based commander brackets
- Theme brackets with a 1.0 theme gate plus bracket-rule-only signal themes
- Theme Report: top z-scoring commanders across every theme, overall or per bracket, with adjustable top counts and default minimums of 200 total decks and 5 theme decks
- Theme Tree: automatically show every selected-theme combination in a zoomable graph with no theme-count cutoff, led by the highest cumulative z-score among commanders with at least 200 total decks, at least 5 decks in every constituent tag, and every tag z-score above 1. Graph calculations run in a background worker; the complete graph grows exponentially and remains subject to browser time and memory limits.
- Quirky Builds: rarest observed builds per theme or theme and bracket, ranked by Build Rarity without a z-score gate; adjustable deck-count and rarity filters
- Sortable and filterable tables
- Z-score, percentile, and rank metrics
- Trend fields across dated snapshots
- Scryfall-based card images and color identity
- Compact card previews on commander-name hover or keyboard focus, including partners, backgrounds, and both faces of double-faced cards
- Accessible colour indicators in commander rows; press Escape to dismiss a preview
- Static frontend deployment through GitHub Pages

## Architecture

Version 1 uses a scheduled batch pipeline instead of a live backend server.

```text
GitHub Actions scheduled workflow
  -> commander discovery
  -> normal EDHREC tag scrape
  -> cEDH special-route scrape
  -> cleaning and validation
  -> statistical analysis
  -> historical trend calculation
  -> Scryfall enrichment
  -> copy latest JSON files into frontend
  -> build React/Vite site
  -> deploy static site to GitHub Pages
```

## Build discovery

The [build-discovery documentation](docs/build_style_prototype.md) explains the
website feature, automatic package-count selection, local analysis and static
data generation. Rolling 30-minute jobs analyze commanders with at least 200 total
EDHREC decks, preserving completed reports and collection checkpoints between runs.
Collection targets every available deck-index ID for eligible commanders, with no
preview count cap. Time slices let unfinished commanders resume across runs. The
200-deck minimum concerns the commander’s total population, not its collected sample.
An eligible commander can still return insufficient usable evidence for packages. Source deck tags are optional. Public EDHREC previews supply cardlists
without direct requests to Moxfield or Archidekt.

Build Discovery starts fresh with zero eligible reports: the earlier 66 visible
reports all had fewer than 200 total decks. New cards appear after qualifying
commanders finish collection and analysis. The minimum is enforced in collection,
export, discovery cards and individual commander package views. Older evidence
remains stored. A visible browser tab refreshes published results and checks the
collector’s GitHub execution status every two minutes.

Commander packages and their Discovery summaries display the largest sampled
deck membership first. Original package numbers stay attached to their generated
decks and mixture weights; ordering does not change the fitted models or collection.
Discovery can filter weighted card signal captured by a minimum/maximum percentage
from 0 to 100, or sort by it. This uses the unrounded Auto-model fit; reports
without a measurement are excluded only when a percentage bound is set.

The distributed collector partitions eligible commanders across separate
GitHub-hosted Ubuntu VMs. Each VM owns its queue, parsed lists, learned request
rate and cooldown; two local workers claim its commanders from opposite ends.
Saved claims resume after a restart. Downloads and model processes run
independently. Source runners use standard headless WebKit to read the embedded
data in ordinary public gallery and preview HTML, using a shared ceiling of 1.5
requests per second per VM by default. A confirmed HTTP302 redirect to EDHREC's
`/rate-limit` route waits at least 60 seconds (or a longer `Retry-After`), then
retries the original URL once with a shared 1-request/second ceiling for the rest
of that VM's job. Repeated throttling pauses collection; unrelated redirects
remain access stops. The temporary cap resets on the next job, while learned
HTTP429 reductions persist. They do not reuse personal browser sessions or solve access
challenges. Existing access holds require an explicit successful review before
collection can resume. The **Test EDHREC browser collection** workflow verifies
one gallery and one 100-card preview without changing production state or
starting other workflows. Build Discovery
reports the combined operating ceiling and measured throughput, rather than
imposing a fixed deadline. Daily
deck-index checks add changed/new versions and remove deleted entries while
preserving unchanged lists and their original download dates. The site separately
shows index verification, deck coverage, pending analysis, and unknown/stale data.
Full automated collection relies on the repository owner's stated permission
from EDHREC. An individual throttle preserves that VM's cooldown; access
restrictions or throttling across multiple VMs stop automatic continuation.
Rate learning preserves existing decks and their original source-check dates.

EDHPowerLevel checks also use two opposite-end workers, keeping each commander's
Auto and numbered package models together through repairs and final checks.
All checks share one request gate and source cooldown on a separate VM while
EDHREC collection continues. One merger publishes the cumulative checkpoint;
power observations attach only to exact deck lists, and repair proposals replace
reports only when their parent hashes still match. Newer collection reports win.

`Collect commander builds on distributed runners` starts with a bounded
two-machine pilot. It requires distinct, stable public IPv4 addresses verified
by two HTTPS services, at least ten minutes of overlapping actual EDHREC
requests, sufficient successes for the pinned rate and duration, at least 80%
of that request rate per VM and 180% combined, no throttles or blocks, and valid
uploaded checkpoints. At the initial 0.1 requests/second and 900-second budget,
this means at least 67 successes per VM, 0.08 successful requests/second per VM
and 0.18 combined. A one-request/second pilot retains the previous thresholds
of 600 successes, 0.8 per VM and 1.8 combined.
Missing evidence fails the rollout gate. A passing pilot measures that observed
pair; it does not establish EDHREC's rate-limit scope for every machine.
Only after reviewing that saved result may `BUILD_STYLE_DISTRIBUTED_RUNNERS` be
set to 16. Full runs then continue with the approved pool size; the pilot never
scales itself. The legacy, distributed, watchdog and resume workflows share the
same coordinator and collection lock.

Each VM receives only its assigned source archive plus shared card metadata.
The merger validates ownership and archive hashes before publishing. Missing
machine results retain the original shard and stop automatic continuation.
Streaming cache merges, disk telemetry and a 2 GiB reserve protect the canonical
checkpoint; worker results remain separately recoverable if a merge fails.

Every supported package can generate a native 100-card deck using learned card
preferences and the sampled card pool, with untagged card lists,
a primer, Moxfield text export and per-card inclusion/lift averages. Bracket estimates and recorded
external ratings are shown separately from card legality checks.

Collector batches continue after durable checkpoint uploads. A separate protected
GitHub environment waits through confirmed HTTP 429 cooldowns before resuming;
its 60-minute wait timer is configured outside the workflow. A metadata-only
ten-minute watchdog recovers idle work without downloading the full dataset.
Each collection worker retains its commander, using saved progress and remaining
work to finish reports promptly. Completed checkpoints explicitly trigger website
publication, including collections started by GitHub Actions itself.
Access restrictions remain distinct from rate-limit pauses.

## Synergy Decks

The separate **Synergy Deck** page (`/#/synergy-deck`) searches the site's commander index and generates a deck for the selected legal configuration. The page also opens from commander detail pages. Partners, Backgrounds, and commanders with a chosen color use their eligible command zone and color identity.
Generation runs in a browser worker using versioned Scryfall, Card Kingdom, and neural model assets. A capped tier requires stocked quotes for the whole deck at or below its ceiling. A commander whose price alone consumes the cap can still have higher-tier lists.

The original verified The Tenth Doctor and Rose Tyler deck remains the starting example. Browser-generated card lists use native mana and role checks, including a local land-and-ramp estimate to guide playability, and link to the exact list in EDHPowerLevel for an external check. The page displays a verified rating when its recorded observation matches that exact list.
The offline ranker learns to recover masked cards using commander and partial-deck features. Construction scores
legal spells from the Scryfall catalog and gives separate credit for effects that start suspend, advance its
countdown, or reward casting from exile. The search keeps the best-scoring candidate it finds under its mana and role targets.

For generated decks, aim for **72.5–77.5% EDHPowerLevel playability**, inclusive. Tune the land count,
colored mana sources, and ramp alongside the deck's mana values and colored costs. Check playability
on the exact generated list. The comparison experiments held the mana base at 37 lands to isolate
changes to spell selection. The uncapped mana-base builder favors early colored sources and untapped typed duals
for fetchlands.

To regenerate, download the `jsonl_download_uri` from the [Scryfall Oracle Cards bulk endpoint](https://api.scryfall.com/bulk-data/oracle-cards)
and use a restored Build Discovery preview checkpoint. Pass the bulk export's `updated_at` value to identify the catalog snapshot.

```bash
python -m edhrec_affinity.synergy_prototype generate \
  --oracle-jsonl /path/to/oracle-cards.jsonl.gz \
  --catalog-updated-at 2026-11-21T22:01:57.459Z \
  --previews data/processed/build-styles-current/_collection/previews \
  --alias-cache data/raw/distinctive-core-card-metadata.json
python -m edhrec_affinity.synergy_prototype check
```

Generation uses the `prototype` Python extra. The check uses the `validation` extra and Playwright Chromium.
It attaches the external result to the exact card list and commander roles. The offline benchmark holds out
card identities and deck records and measures recovery of known inclusions among sampled legal candidates.

The budget selector offers **$** (up to $100 USD), **$$** (up to $500), **$$$** (up to $1,000), and
**$$$$** (uncapped). Capped lists use Card Kingdom prices for all 100 cards, including both commanders and
every copy of each basic land. The shopping list picks the cheapest in-stock English printings eligible for paper play,
including foil copies where they cost less. It can split quantities across printings and conditions. The reported total is
the dated singles subtotal before tax and shipping. Each exact list has its own playability result.

To rebuild the tiers, save the [Card Kingdom singles feed](https://api.cardkingdom.com/api/v2/pricelist) as JSON,
and obtain Scryfall's Default Cards and Oracle Cards gzipped JSONL exports.

```bash
python -m edhrec_affinity.synergy_prototype.prices \
  --price-feed /path/to/card-kingdom.json \
  --printing-jsonl /path/to/default-cards.jsonl.gz \
  --oracle-jsonl /path/to/oracle-cards.jsonl.gz \
  --output /path/to/deck-prices.json
python -m edhrec_affinity.synergy_prototype.budgets generate \
  --prices /path/to/deck-prices.json \
  --oracle-jsonl /path/to/oracle-cards.jsonl.gz \
  --catalog-updated-at 2026-11-21T22:01:57.459Z \
  --previews data/processed/build-styles-current/_collection/previews \
  --alias-cache data/raw/distinctive-core-card-metadata.json \
  --exports-directory data/prototypes/synergy-deck/budgets
python -m edhrec_affinity.synergy_prototype.budgets check
```

Run `python -m edhrec_affinity.synergy_prototype.budgets reprice --prices /path/to/deck-prices.json`
to update shopping choices for the existing deck lists.

To refresh the public worker's card and price snapshot while reusing the trained model, run

```bash
python -m edhrec_affinity.synergy_decks.assets \
  --oracle-jsonl /path/to/oracle-cards.jsonl.gz \
  --catalog-updated-at YYYY-MM-DDTHH:MM:SSZ \
  --prices /path/to/deck-prices.json \
  --printings-jsonl /path/to/default-cards.jsonl.gz \
  --reuse-model-from frontend/public/synergy-assets
```

The standard GitHub Pages build publishes these versioned assets. Newly added cards get Oracle-text scores until the model is retrained. Use `--previews` in place of `--reuse-model-from` to train from saved deck previews.

The [experiment report](frontend/src/generated/synergy-deck-experiments.json) preserves the original comparison
and all exact-list EDHPowerLevel assessments. The offline experiment command uses the archived baseline and compares
changes to the Oracle scoring, the ramp penalty, the model's representation of the partial deck, and all three together.

```bash
python -m edhrec_affinity.synergy_prototype.experiments generate \
  --oracle-jsonl /path/to/oracle-cards.jsonl.gz \
  --catalog-updated-at 2026-11-21T22:01:57.459Z \
  --previews data/processed/build-styles-current/_collection/previews \
  --alias-cache data/raw/distinctive-core-card-metadata.json \
  --exports-directory data/prototypes/synergy-deck/experiments
python -m edhrec_affinity.synergy_prototype.experiments check
```
