# Data Source Validation Decision

## Decision

The project will use EDHREC network JSON files as the primary source for commander total dekc counts and tag deck counts.

## Commander Discovery

Use:

https://edhrec.com/sitemaps/commanders.xml

This sitemap provides commander page URLs. The scraper will prase commander slugs form these URLs.

## Commander Data

For each commander, retrieve the network JSON file associated with that commander slug.

Important fields:

- `num_decks_avg`: total number of commander decks
- `taglinks`: complete list of available commander tags

Eeach `taglinks` entry contains:

- `count`: number of decks for that commander-tag combination
- `slug`: tag slugs
- `value`: display name of the tag

## cEDH Exception

cEDH is not treated like a normal visible tag. EDHREC exposes cEDH through a special filtered route:

/commanders/<commander-slug>/cedh

For example:

/commanders/the-tenth-doctor-rose-tyler/cedh

The scraper should capture the cEDH-specific JSON/page and extract its deck count separately..

## Playwright Decision

Playwright is not required for the normal scraper path.

It may still be used for debugging, validating network requests, or checking future EDHREC frontend changes.