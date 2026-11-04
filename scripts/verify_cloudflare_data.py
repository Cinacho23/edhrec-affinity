#!/usr/bin/env python3
"""Verify a specific Cloudflare asset version before publishing its Pages UI."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import brotli
import httpx

ORIGIN = "https://cinacho23.github.io"


def version_url(deployment_url: str, command_output: str) -> str:
    url = urlsplit(deployment_url.strip())
    if (url.scheme != "https" or url.username or url.password or url.port
            or not re.fullmatch(r"edhrec-affinity-data\.[a-z0-9-]+\.workers\.dev", url.hostname or "")
            or url.path not in {"", "/"} or url.query or url.fragment):
        raise ValueError("Expected the edhrec-affinity-data workers.dev deployment URL")
    match = re.search(r"Current Version ID:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", command_output, re.I)
    if not match:
        raise ValueError("Wrangler did not report a version ID; refusing to publish an unpinned data URL")
    return f"https://{match[1][:8].lower()}-{url.hostname}"


def verify_data(root: Path, base_url: str, *, client: httpx.Client) -> None:
    # Verify identity, collector-facing indexes and one real report, plus the
    # largest asset. A successful CLI upload alone does not prove public CORS access.
    paths = [root / "deployment.json", root / "data/latest/site_manifest.json",
             root / "data/latest/commanders/index.json", root / "data/latest/build-styles/index.json.br"]
    reports = sorted((root / "data/latest/build-styles").glob("*.json.br"))
    paths.extend(path for path in reports if path.name != "index.json.br")
    paths = paths[:5]
    assets = [path for path in root.rglob("*") if path.is_file() and path.name != "_headers"]
    paths.append(max(assets, key=lambda path: path.stat().st_size))
    for path in dict.fromkeys(paths):
        if not path.is_file():
            raise ValueError(f"Missing required exported asset: {path}")
        response = client.get(f"{base_url}/{path.relative_to(root).as_posix()}", headers={"Origin": ORIGIN})
        response.raise_for_status()
        if response.headers.get("access-control-allow-origin") not in {"*", ORIGIN}:
            raise ValueError(f"Cloudflare asset is not readable from GitHub Pages: {path.name}")
        expected = path.read_bytes()
        # httpx, like browser fetch, removes an HTTP Content-Encoding itself.
        if path.suffix == ".br" and response.headers.get("content-encoding") == "br":
            expected = brotli.decompress(expected)
        if hashlib.sha256(response.content).digest() != hashlib.sha256(expected).digest():
            raise ValueError(f"Published Cloudflare asset does not match this build: {path.name}")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--asset-dir", type=Path, default=Path("data/cloudflare-static"))
    cli.add_argument("--pages-data-dir", type=Path, default=Path("frontend/public/data/latest"))
    args = cli.parse_args()
    base = version_url(os.environ["DEPLOYMENT_URL"], os.environ["WRANGLER_COMMAND_OUTPUT"])
    with httpx.Client(timeout=90, follow_redirects=False) as client:
        for attempt in range(6):
            try:
                verify_data(args.asset_dir, base, client=client)
                break
            except (httpx.HTTPError, ValueError):
                if attempt == 5:
                    raise
                time.sleep(10)
    data_base = f"{base}/data/latest"
    # Existing tooling starts at Pages' plain manifest and can discover the
    # same immutable data snapshot used by the browser.
    manifest_path = args.pages_data_dir / "site_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["data_base_url"] = data_base
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"data-base-url={data_base}\n")
    print(f"Verified Cloudflare data snapshot: {data_base}")


if __name__ == "__main__":
    main()
