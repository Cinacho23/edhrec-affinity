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

## Cloudflare data hosting

The compressed export eventually outgrew Pages too. Both publishers now support
hosting public data with **Workers Static Assets**, while keeping the app and its
existing URL on GitHub Pages. Collection and saved checkpoints are unchanged.

Add these GitHub repository **Actions secrets**:

- `CLOUDFLARE_API_TOKEN`: an Edit Cloudflare Workers token restricted to the
  account used for this project.
- `CLOUDFLARE_ACCOUNT_ID`: that account's ID.

No Cloudflare Git build integration, domain, R2 bucket, or paid plan is required.
The existing GitHub workflows create `edhrec-affinity-data` using
`cloudflare/wrangler.json`. Do not configure a separate Cloudflare build from
Git: the generated data comes from the restored GitHub Actions checkpoints.
Never commit the token or put it in a frontend environment variable.

When both secrets exist, the shared `publish-static-data` action:

1. Stages only the exported public data in `data/cloudflare-static`, applies the
   existing publication projection and verified Brotli compression, and checks
   Free limits (20,000 files and 25 MiB per file). Oversized files fail with an
   instruction to split them before publishing; this change does not truncate
   packages or silently upgrade the account.
2. Uploads assets with Cloudflare's official Wrangler action. No request handler
   is deployed; the files use static asset serving, without per-request Worker
   execution. Content hashes let Wrangler reuse unchanged uploaded assets.
3. Derives a version URL from the deployed Worker version, then verifies the
   build identity, indexes, a real report and the largest asset, including CORS
   headers and exact published bytes. It retries propagation failures, then
   stops before the Pages upload if verification fails.
4. Builds the UI with that verified `VITE_SITE_DATA_BASE_URL`. All resources use
   the same pinned version, so a later data upload cannot switch an already
   published frontend to a partially uploaded snapshot. Discovery's existing
   index polling checks the small Pages manifest and switches open tabs to the
   newly published version of the same Worker/account. Cloudflare versions remain
   available subject to Cloudflare's version retention; rollback the Pages UI
   only to versions whose data URL still exists.
5. Leaves plain operational indexes and the manifest on Pages for collectors.
   Pages' manifest advertises `data_base_url`, which the development downloader
   follows. The remaining Pages payload still passes the 950 MB guard.

If neither secret exists, publication retains the original same-origin behavior
and size guard. If exactly one is set, it fails with setup instructions. Once
configured, a Cloudflare failure does not fall back to publishing an oversized
site or replace the current working Pages deployment.

After adding the secrets and merging the configuration, run **Deploy frontend
to GitHub Pages** to publish the latest saved data. The weekly full-data workflow
uses the same action. There is no need to restart collection.

Cloudflare references: [GitHub Actions authentication](https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/),
[static asset limits](https://developers.cloudflare.com/workers/platform/limits/#static-assets),
[static asset pricing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/),
[Version URLs](https://developers.cloudflare.com/workers/versions-and-deployments/version-urls/).

The manual **Audit saved Pages payload size** workflow measures an existing
`github-pages` artifact without extracting the full site onto disk. It partitions
compression measurement across eight read-only jobs. This is a diagnostic only;
each production publish checks its actual final files before uploading them.

References: [Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits),
[custom workflow artifact requirements](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
