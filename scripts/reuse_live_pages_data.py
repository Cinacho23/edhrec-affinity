#!/usr/bin/env python3
"""Preserve the published Pages indexes and pin their verified Cloudflare snapshot."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.request import HTTPRedirectHandler, Request, build_opener

import brotli


PAGES_ORIGIN = "https://cinacho23.github.io"
PAGES_DATA = f"{PAGES_ORIGIN}/edhrec-affinity/data/latest"
WORKER_HOST = "edhrec-affinity-data.antoniocinottiballa.workers.dev"
MANIFEST = "site_manifest.json"
INDEXES = (
    "build-styles/index.json",
    "commanders/index.json",
    "leaderboard/index.json",
    "sets/index.json",
    "tags/index.json",
    "theme-brackets/index.json",
)
MAX_FILE_BYTES = 128 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def fetch(url, *, from_worker=False):
    headers = {"Cache-Control": "no-cache", "Accept-Encoding": "identity",
               "User-Agent": "edhrec-affinity frontend publisher"}
    if from_worker:
        headers["Origin"] = PAGES_ORIGIN
    with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=90) as response:
        if response.status != 200:
            raise ValueError(f"Expected HTTP 200 from {url}, got {response.status}")
        if from_worker and response.headers.get("Access-Control-Allow-Origin") not in {"*", PAGES_ORIGIN}:
            raise ValueError(f"Worker asset is missing Pages CORS access: {url}")
        body = response.read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES:
        raise ValueError(f"Bootstrap file exceeds its size limit: {url}")
    return body


def published_manifest(version):
    if not re.fullmatch(r"[0-9a-f]{8}", version):
        raise ValueError("Expected the eight lowercase hex characters of the live Cloudflare version")
    data_base = f"https://{version}-{WORKER_HOST}/data/latest"
    raw = fetch(f"{PAGES_DATA}/{MANIFEST}")
    manifest = json.loads(raw)
    if manifest.pop("data_base_url", None) != data_base:
        raise ValueError("Live Pages manifest does not point at the requested immutable Cloudflare version")
    return raw, manifest, data_base


def verified_pair(path, data_base):
    plain = fetch(f"{PAGES_DATA}/{path}")
    encoded = fetch(f"{PAGES_DATA}/{path}.br")
    if brotli.decompress(encoded) != plain:
        raise ValueError(f"Published Pages Brotli file differs from its JSON: {path}")
    if path != MANIFEST:
        worker_encoded = fetch(f"{data_base}/{path}.br", from_worker=True)
        if brotli.decompress(worker_encoded) != plain:
            raise ValueError(f"Published Pages index differs from the pinned Worker: {path}")
    return plain, encoded


def prepare(destination, version):
    if destination.exists():
        raise ValueError("Use a fresh destination for the verified Pages bootstrap")
    raw, clean_manifest, data_base = published_manifest(version)
    worker_manifest = fetch(f"{data_base}/{MANIFEST}", from_worker=True)
    version_root = data_base.removesuffix("/data/latest")
    deployment = json.loads(fetch(f"{version_root}/deployment.json", from_worker=True))
    if (deployment.get("schema_version") != 1
            or not re.fullmatch(r"\d+-\d+", deployment.get("build_id", ""))
            or deployment.get("manifest_sha256") != hashlib.sha256(worker_manifest).hexdigest()
            or clean_manifest != json.loads(worker_manifest)):
        raise ValueError("Published Worker manifest and deployment metadata do not match Pages")

    paths = (MANIFEST, *INDEXES)
    with ThreadPoolExecutor(max_workers=4) as pool:
        pairs = dict(zip(paths, pool.map(lambda path: verified_pair(path, data_base), paths), strict=True))
    if pairs[MANIFEST][0] != raw or published_manifest(version)[0] != raw:
        raise ValueError("The live Pages snapshot changed during verification")

    for path, (plain, encoded) in pairs.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(plain)
        target.with_suffix(".json.br").write_bytes(encoded)
    return data_base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.check_only:
        raw, _, data_base = published_manifest(args.version)
        if raw != (args.destination / MANIFEST).read_bytes():
            raise ValueError("The live Pages snapshot changed after preparing the frontend")
    else:
        data_base = prepare(args.destination, args.version)
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"data-base-url={data_base}\n")
    print(f"Verified existing Pages data snapshot: {data_base}")


if __name__ == "__main__":
    main()
