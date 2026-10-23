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
data generation. Real seed samples cover Ghave, Hapatra and Jasmine. Rolling
four-hour jobs continue through the full commander catalog, preserving completed
reports and collection checkpoints between runs, with an hourly recovery schedule.
Collection targets every
available deck-index ID, with no commander or preview count cap. Time slices let
unfinished commanders resume across runs. There is no minimum popularity gate. Low-data
commanders can return profiles or an insufficient-data result instead of card
packages. Source deck tags are optional. Public EDHREC previews supply cardlists
without direct requests to Moxfield or Archidekt.

Build Discovery displays reports completed by the repaired collector, starting
with the 25 reports saved by run 39893813350. More cards appear as collection
finishes for other commanders. Older reports remain stored for commander pages.

Downloads and model processes run independently, using compact public JSON or
React preview data and one adaptive shared request ceiling. The collector learns
an operating rate below EDHREC's throttling threshold and remembers it across jobs.
Build Discovery reports the fastest collection estimate at that rate and a
projection from measured throughput, rather than imposing a fixed deadline. Daily
deck-index checks add changed/new versions and remove deleted entries while
preserving unchanged lists and their original download dates. The site separately
shows index verification, deck coverage, pending analysis, and unknown/stale data.
Full automated collection relies on the repository owner's stated permission
from EDHREC; access restrictions and server cooldowns stop all source workers.
Rate learning preserves existing decks and their original source-check dates.

Every supported package can generate a native 100-card deck using learned card
preferences and the sampled card pool, with one primary category tag per card,
a primer, Moxfield text export and per-card inclusion/lift averages. Bracket estimates and recorded
external ratings are shown separately from card legality checks.
