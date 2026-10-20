# Build-discovery seed samples

These are real samples collected from public EDHREC profiles and deck previews
on October 19, 2026 (America/Toronto). Each has 12 tag profiles and 60 unique
sampled decks. The original deck URLs, sampling seed, collection provenance,
model diagnostics and supported alternative counts are included in each report.

Automatic selection suggests two packages for Ghave and one each for Hapatra
and Jasmine. These are exploratory sample-based suggestions, not population
estimates or definitive counts of strategies.

The October 20 revision adds 66 generated deck entries across the automatic and
supported alternative models. Each contains 100 cards, tagged import text,
per-card inclusion/lift averages and a primer. The four automatically suggested
decks have observed EDHPowerLevel playability in the 72.5–77.5% target; alternative
models display their own results and may fall outside that range.

`validations/` stores dated external observations keyed by the exact card-list
hash, including separately checked candidate lists. Only matching observations
can attach to a report, and seven-day-old observations are shown as stale.

The static exporter uses these files to populate initial website coverage and
prefers newer successful generated reports. Local cache paths have been removed.
See [the methodology and generation instructions](../../docs/build_style_prototype.md).
