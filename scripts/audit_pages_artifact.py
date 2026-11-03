#!/usr/bin/env python3
"""Stream a saved Pages ZIP/tar and report sizes without extracting site data."""
import argparse
from collections import defaultdict
import heapq
import hashlib
import io
import json
import os
import re
import struct
import zlib

import httpx
import brotli
from edhrec_affinity.site_payload import project_build_report
import tarfile


class Reader(io.RawIOBase):
    def __init__(self, chunks):
        self.chunks, self.buffer = iter(chunks), bytearray()

    def read(self, size=-1):
        while size < 0 or len(self.buffer) < size:
            chunk = next(self.chunks, None)
            if chunk is None:
                break
            self.buffer.extend(chunk)
        count = len(self.buffer) if size < 0 else min(size, len(self.buffer))
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result


def tar_chunks(reader):
    header = reader.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise ValueError("Expected a normal artifact ZIP local header")
    _, _, flags, method, _, _, _, _, _, name_size, extra_size = struct.unpack("<4s5H3I2H", header)
    name = reader.read(name_size)
    reader.read(extra_size)
    if flags & 1 or name != b"artifact.tar" or method not in {0, 8}:
        raise ValueError("Expected the single unencrypted Pages artifact.tar")
    decoder = zlib.decompressobj(-15) if method == 8 else None
    while chunk := reader.read(1024 * 1024):
        yield decoder.decompress(chunk) if decoder else chunk
        if decoder and decoder.eof:
            return


def audit(chunks, *, shard=0, shards=1, quality=10):
    totals, largest, samples = defaultdict(lambda: {"files": 0, "bytes": 0, "brotli_bytes": 0}), [], []
    with tarfile.open(fileobj=Reader(tar_chunks(Reader(chunks))), mode="r|") as archive:
        for member in archive:
            if not member.isfile():
                continue
            bucket = int.from_bytes(hashlib.sha256(member.name.encode()).digest()[:4]) % shards
            if bucket != shard:
                continue
            parts = member.name.removeprefix("./").split("/")
            group = "/".join(parts[:3]) if parts[:2] == ["data", "latest"] else parts[0]
            totals[group]["files"] += 1
            totals[group]["bytes"] += member.size
            heapq.heappush(largest, (member.size, member.name))
            if len(largest) > 12:
                heapq.heappop(largest)
            source = archive.extractfile(member)
            if group == "data/latest/build-styles" and member.name.endswith(".json") and parts[-1] != "index.json":
                projected = json.dumps(project_build_report(json.load(source)), ensure_ascii=False,
                                       allow_nan=False, separators=(",", ":")).encode()
                source = io.BytesIO(projected)
            compressor = brotli.Compressor(quality=quality, lgwin=24)
            compressed = 0
            capture = (group == "data/latest/build-styles" and member.name.endswith(".json")
                       and member.size <= 16 * 1024 * 1024 and len(samples) < 5)
            raw = bytearray() if capture else None
            while chunk := source.read(1024 * 1024):
                compressed += len(compressor.process(chunk))
                if raw is not None:
                    raw.extend(chunk)
            compressed += len(compressor.finish())
            totals[group]["brotli_bytes"] += compressed if member.name.endswith(".json") else member.size
            # The publisher retains operational indexes as plain JSON too.
            if parts[-1] in {"index.json", "site_manifest.json"}:
                totals[group]["brotli_bytes"] += member.size
            if raw is not None:
                data = json.loads(raw)
                analysis = data.get("deck_analysis", {})
                samples.append({"file": member.name, "bytes": member.size, "brotli_bytes": compressed,
                    "models": len(analysis.get("alternative_models", [])),
                    "fields": {key: len(json.dumps(value, separators=(",", ":"))) for key, value in data.items()},
                    "analysis": {key: len(json.dumps(value, separators=(",", ":"))) for key, value in analysis.items()}})
    return {"groups": dict(totals), "largest": sorted(largest, reverse=True), "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-id", required=True, type=int)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--quality", type=int, default=10, choices=range(12))
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or args.artifact_id <= 0
            or not 0 <= args.shard < args.shards <= 16):
        raise ValueError("Invalid artifact identity")
    with httpx.Client(timeout=120) as client:
        response = client.get(f"https://api.github.com/repos/{repository}/actions/artifacts/{args.artifact_id}/zip",
            headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json"})
        if response.status_code != 302:
            response.raise_for_status()
            raise ValueError("Expected the ordinary GitHub artifact redirect")
        location = response.headers["location"]
        if httpx.URL(location).scheme != "https":
            raise ValueError("Artifact storage must use HTTPS")
        # The redirected storage request carries no GitHub authentication.
        with client.stream("GET", location) as stream:
            stream.raise_for_status()
            result = audit(stream.iter_bytes(chunk_size=1024 * 1024), shard=args.shard, shards=args.shards, quality=args.quality)
    for key, value in result.items():
        print(f"::notice title=Pages size audit {key}::{json.dumps(value, separators=(',', ':'))}")


if __name__ == "__main__":
    main()
