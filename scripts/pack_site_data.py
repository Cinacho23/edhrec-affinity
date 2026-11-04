#!/usr/bin/env python3
"""Losslessly encode deployed JSON and enforce the GitHub Pages size budget.

Run against the built dist directory, never the collector's source/checkpoints.
The browser loader understands .json.br; operational indexes remain readable as
plain JSON too. Compression and verification stream one file per worker.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import stat

import brotli
from edhrec_affinity.site_payload import project_build_report


PAGES_BUDGET_BYTES = 950_000_000
CHUNK_BYTES = 1024 * 1024


def encode_json(source: Path) -> tuple[int, int]:
    target = source.with_suffix(source.suffix + ".br")
    temporary = target.with_suffix(target.suffix + ".tmp")
    compressor = brotli.Compressor(quality=10, lgwin=24)
    original_hash, original_size = hashlib.sha256(), 0
    try:
        with source.open("rb") as incoming, temporary.open("wb") as outgoing:
            if source.parent.name == "build-styles" and source.name != "index.json":
                # Publication-only precision/audit projection. The source JSON
                # and original checkpoints are not changed by this operation.
                raw = json.dumps(project_build_report(json.load(incoming)), ensure_ascii=False,
                                 allow_nan=False, separators=(",", ":")).encode("utf-8")
                chunks = (raw[offset:offset + CHUNK_BYTES] for offset in range(0, len(raw), CHUNK_BYTES))
            else:
                chunks = iter(lambda: incoming.read(CHUNK_BYTES), b"")
            for chunk in chunks:
                original_hash.update(chunk)
                original_size += len(chunk)
                outgoing.write(compressor.process(chunk))
            outgoing.write(compressor.finish())
        # Verify every encoded byte stream before removing the dist JSON copy.
        decoder, restored_hash, restored_size = brotli.Decompressor(), hashlib.sha256(), 0
        with temporary.open("rb") as incoming:
            while chunk := incoming.read(CHUNK_BYTES):
                restored = decoder.process(chunk)
                restored_hash.update(restored)
                restored_size += len(restored)
        if (not decoder.is_finished() or original_size != restored_size
                or original_hash.digest() != restored_hash.digest()):
            raise ValueError(f"Lossless verification failed: {source}")
        temporary.replace(target)
        if source.name not in {"index.json", "site_manifest.json"}:
            source.unlink()
        return original_size, target.stat().st_size
    finally:
        temporary.unlink(missing_ok=True)


def site_sizes(root: Path) -> dict:
    groups: dict[str, int] = {}
    total, count, largest = 0, 0, []
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink > 1):
            raise ValueError(f"Pages payload contains a link: {path.relative_to(root)}")
        if not stat.S_ISREG(info.st_mode):
            continue
        relative = path.relative_to(root).as_posix()
        group = "/".join(relative.split("/")[:3])
        groups[group] = groups.get(group, 0) + info.st_size
        total += info.st_size
        count += 1
        largest.append((info.st_size, relative))
    return {"files": count, "bytes": total,
            "tar_budget_bytes": total + count * 2048 + 10240,
            "groups": groups, "largest": sorted(largest, reverse=True)[:12]}


def pack_site(root: Path, *, max_bytes: int | None = PAGES_BUDGET_BYTES, workers: int = 2) -> dict:
    root = root.resolve()
    data_dir = root / "data" / "latest"
    if not (data_dir / "site_manifest.json").is_file():
        raise ValueError("Expected a built site with data/latest/site_manifest.json")
    if not 1 <= workers <= 4 or (max_bytes is not None and max_bytes <= 0):
        raise ValueError("Invalid compression worker count or size budget")
    # Reject links before following or modifying anything in the build.
    before = site_sizes(root)
    sources = sorted(data_dir.rglob("*.json"))
    raw_bytes, encoded_bytes = 0, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for number, (raw, encoded) in enumerate(pool.map(encode_json, sources), 1):
            raw_bytes += raw
            encoded_bytes += encoded
            if number % 250 == 0:
                print(f"Encoded and verified {number}/{len(sources)} JSON files", flush=True)
    result = site_sizes(root)
    result.update({"before_bytes": before["bytes"], "json_bytes": raw_bytes,
                   "encoded_json_bytes": encoded_bytes, "max_bytes": max_bytes})
    print(json.dumps(result, indent=2), flush=True)
    if max_bytes is not None and result["tar_budget_bytes"] > max_bytes:
        raise ValueError(f"Pages payload exceeds the safe {max_bytes:,}-byte budget: "
                         f"{result['bytes']:,} file bytes; {result['tar_budget_bytes']:,} including archive allowance")
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--site-dir", type=Path, default=Path("frontend/dist"))
    cli.add_argument("--max-bytes", type=int, default=PAGES_BUDGET_BYTES)
    cli.add_argument("--workers", type=int, default=2, choices=range(1, 5))
    args = cli.parse_args()
    result = pack_site(args.site_dir, max_bytes=args.max_bytes, workers=args.workers)
    print(f"::notice title=Verified Pages payload::{result['bytes']:,} bytes; "
          f"{result['files']} files; all published JSON compression verified lossless")


if __name__ == "__main__":
    main()
