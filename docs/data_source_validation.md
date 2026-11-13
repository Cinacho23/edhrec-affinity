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

- `container.json_dict.card.num_decks`: total number of commander decks
- `num_decks_avg`: legacy total-deck field retained as a compatibility fallback
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

Playwright is not required for the aggregate commander/tag scraper. The
individual-deck build-style collector now has a separately selected standard
headless WebKit transport, enabled on source collection runners with
`BUILD_STYLE_EDHREC_TRANSPORT=webkit`. It reads ordinary public gallery and
preview HTML, using the embedded data already sent for those pages. It does not
reuse a person's browser session or solve access challenges.

Install the `collector` optional dependency and browser on a collection machine:

```bash
python -m pip install -e ".[prototype,collector]"
python -m playwright install --with-deps webkit
```

The explicit **Test EDHREC browser collection** workflow checks one known
commander gallery and one known 100-card preview through the production cache
and parser. It spaces requests ten seconds apart and uploads the proof even
when access fails. This workflow cannot release collection holds or dispatch
full collection. A successful local test does not establish GitHub-hosted
access, a sustainable rate or complete coverage of other commanders.

On November 13 the separate bounded GitHub-runner rate test completed all
five stages through one request/second: 419 distinct previews and one gallery
returned HTTP200, with no 403, 429, or challenge. Its peak stage retrieved 240
decks in four minutes. The user then approved one request/second per worker;
new production batches use that default with shared pacing within each VM.
Existing 0.1 checkpoint budgets receive a one-time audited promotion, while
current source holds and new throttle reductions remain authoritative.
