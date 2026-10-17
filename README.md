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
- Global commander-tag leaderboard
- Tag explorer
- Set-based commander brackets
- Theme brackets with a 1.0 theme gate plus bracket-rule-only signal themes
- Theme Report: top z-scoring commanders across every theme, overall or per bracket, with adjustable top counts and default minimums of 200 total decks and 5 theme decks
- Theme Tree: explore every selected-theme combination in a zoomable graph, led by the highest cumulative z-score with every constituent tag above 1; large selections expand on demand
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
