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

The distributed collector partitions eligible commanders across separate
GitHub-hosted Ubuntu VMs. Each VM owns its queue, parsed lists, learned request
rate and cooldown; two local workers claim its commanders from opposite ends.
Saved claims resume after a restart. Downloads and model processes run
independently, using compact public JSON or React preview data. Build Discovery
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
requests, at least 600 successes per VM, at least 0.8 successful requests/second
per VM and 1.8 combined, no throttles or blocks, and valid uploaded checkpoints.
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
