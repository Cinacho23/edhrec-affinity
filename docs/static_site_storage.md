# Static site storage

GitHub Pages limits a published site to 1 GB. Its deployment also rejects an
uncompressed Pages artifact at 10 GB. The November 2, 2026 export reached about
10.8 GB of files (2.51 GB for the uploaded ZIP), including 6.82 GB of build reports.
Increasing a job timeout cannot fix that failure.

Both publication workflows build the app with `VITE_SITE_DATA_ENCODING=br` and
then run `scripts/pack_site_data.py` against `frontend/dist`. JSON resources are
stored as `.json.br`. The browser fetches and decodes them with a bundled Brotli
WebAssembly decoder. The loader supports plain JSON deployments and local data,
and the development downloader supports both formats. Paths inside indexes and
the manifest remain logical `.json` paths. Operational indexes and the manifest
also remain available as plain JSON for existing tooling.

Build reports receive a publication-only projection. Request audit history
(`collection.provenance`) remains in collector checkpoints, while the public
report retains source links, source-check summaries, coverage and freshness.
Only individual deck mixture weights are normally rounded to six decimal places.
Exact values are retained when rounding would change the configured membership
cutoff, a displayed whole percentage, or deck ordering in any package. All model
counts, sample deck rows, package definitions, generated decks, primers, card
statistics, scores and restriction evidence remain available. This projection
does not modify checkpoints, training inputs or deck generation.

The packer processes at most two files concurrently by default and verifies the
decompressed publication bytes against their precompression SHA-256 hashes.
It rejects links, reports stored sizes by directory, and stops before upload
when stored files plus a conservative archive allowance exceed 950,000,000
bytes. This guard is intentionally below the Pages limit. If future collection
outgrows that budget, publication must be reduced further or moved to larger
static storage; the collector's saved progress is unaffected.

For a local production check:

```sh
python -m pip install -e '.[dev]'
cd frontend
npm ci
VITE_SITE_DATA_ENCODING=br npm run build
cd ..
python scripts/pack_site_data.py --site-dir frontend/dist
```

The manual **Audit saved Pages payload size** workflow measures an existing
`github-pages` artifact without extracting the full site onto disk. It partitions
compression measurement across eight read-only jobs. This is a diagnostic only;
each production publish checks its actual final files before uploading them.

References: [Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits),
[custom workflow artifact requirements](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
