#!/usr/bin/env python3
"""Stage generated public data for Cloudflare; leave operational indexes on Pages.

Only exported public files are moved. Collector checkpoints are never inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from pack_site_data import pack_site, site_sizes

MAX_ASSETS = 20_000
MAX_ASSET_BYTES = 25 * 1024 * 1024


def validate_assets(root: Path, *, max_files: int = MAX_ASSETS,
                    max_file_bytes: int = MAX_ASSET_BYTES) -> dict:
    sizes = site_sizes(root)  # Also rejects links.
    if sizes["files"] > max_files:
        raise ValueError(f"Cloudflare Free supports {max_files:,} assets; found {sizes['files']:,}")
    oversized = [(size, name) for size, name in sizes["largest"] if size > max_file_bytes]
    if oversized:
        raise ValueError(f"Cloudflare assets exceed {max_file_bytes:,} bytes per file; split before publishing: {oversized}")
    return sizes


def prepare_data(data_dir: Path, output_dir: Path, *, build_id: str,
                 max_asset_bytes: int = MAX_ASSET_BYTES) -> dict:
    data_dir, output_dir = data_dir.resolve(), output_dir.resolve()
    if not (data_dir / "site_manifest.json").is_file():
        raise ValueError("Expected an exported public data directory with site_manifest.json")
    if not build_id or max_asset_bytes <= 0 or output_dir.exists() or output_dir == data_dir or output_dir.is_relative_to(data_dir) or data_dir.is_relative_to(output_dir):
        raise ValueError("Use a fresh, separate output directory and a nonempty build ID")
    site_sizes(data_dir)  # Reject links before moving anything.
    target = output_dir / "data/latest"
    target.parent.mkdir(parents=True)
    shutil.move(str(data_dir), target)
    # Cloudflare has per-file and asset-count limits, rather than Pages' 1 GB total.
    pack_site(output_dir, max_bytes=None)
    manifest_hash = hashlib.sha256((target / "site_manifest.json").read_bytes()).hexdigest()
    metadata = {"schema_version": 1, "build_id": build_id, "manifest_sha256": manifest_hash}
    (output_dir / "deployment.json").write_text(json.dumps(metadata), encoding="utf-8")
    # Public JSON assets are read cross-origin from the existing GitHub Pages UI.
    # .br files are opaque bytes decoded by the browser's existing WASM decoder.
    (output_dir / "_headers").write_text(
        "/*\n  Access-Control-Allow-Origin: *\n  Access-Control-Expose-Headers: Content-Encoding\n"
        "  Cache-Control: public, max-age=0, must-revalidate\n"
        "  X-Content-Type-Options: nosniff\n", encoding="utf-8")
    # Keep existing collector URLs and every operational index readable on Pages.
    omitted_plain_indexes = []
    for source in target.rglob("*.json"):
        if source.name not in {"index.json", "site_manifest.json"}:
            continue
        destination = data_dir / source.relative_to(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        # Pages needs plain operational indexes, while the browser and local
        # downloader support .json.br. An optional plain duplicate must not
        # prevent publishing an otherwise valid compressed Cloudflare dataset.
        if source.name == "index.json" and source.stat().st_size > max_asset_bytes:
            encoded = source.with_suffix(".json.br")
            if not encoded.is_file():
                raise ValueError(f"Cannot omit a plain index without its encoded copy: {source}")
            source.unlink()
            omitted_plain_indexes.append(source.relative_to(output_dir).as_posix())
    sizes = validate_assets(output_dir, max_file_bytes=max_asset_bytes)
    print(json.dumps({**metadata, "assets": sizes,
                      "plain_indexes_kept_only_on_pages": omitted_plain_indexes}, indent=2), flush=True)
    return metadata


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--data-dir", type=Path, default=Path("frontend/public/data/latest"))
    cli.add_argument("--output-dir", type=Path, default=Path("data/cloudflare-static"))
    cli.add_argument("--build-id", required=True)
    args = cli.parse_args()
    prepare_data(args.data_dir, args.output_dir, build_id=args.build_id)


if __name__ == "__main__":
    main()
