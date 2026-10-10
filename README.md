# EDHREC Commander Tag Affinity Analysis

A static data-analysis website for exploring which Magic: The Gathering Commander decks are unusually associated with EDHREC tags.

The project collects commander deck/tag data, computes tag-affinity metrics, tracks changes across snapshots, enriches commanders with card metadata, and publishes the results through a searchable React/Vite website.

## Research question

Which commanders are unusually associated with specific EDHREC tags after adjusting for commander popularity, tag-wide baseline behavior, and sample size?

This project is not trying to reproduce EDHREC. Its purpose is to add an original statistical layer on top of commander/tag counts.

## Main features

- Commander search
- Commander detail pages with a minimum of 5 tagged decks per displayed tag
- Global commander-tag leaderboard
- Tag explorer
- Set-based commander brackets
- Theme brackets with a 1.05 theme gate plus bracket-rule-only signal themes
- Theme Report: top z-scoring commanders across every theme, overall or per bracket, with adjustable top counts and default minimums of 200 total decks and 5 theme decks
- Quirky Builds: rarest observed builds per theme or theme and bracket, ranked by Build Rarity without a z-score gate; adjustable deck-count and rarity filters
- Sortable and filterable tables
- Z-score, percentile, and rank metrics
- Trend fields across dated snapshots
- Scryfall-based card images and color identity
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
