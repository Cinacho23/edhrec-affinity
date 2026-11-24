"""Export a trained card ranker, Oracle catalog, and price quotes for Pages."""

import argparse
from base64 import b64decode, b64encode
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import numpy as np

from ..build_styles.deckbuilding import (
    _can_command, _chooses_commander_color, _copy_limit, _is_basic, _is_land, _oracle, functional_tags,
)
from ..build_styles.sources import card_key
from ..synergy_prototype.__main__ import commander_keys
from ..synergy_prototype.budgets import load_snapshot
from ..synergy_prototype.catalog import load_catalog
from ..synergy_prototype.construction import _land_colors, _land_quality
from ..synergy_prototype.prices import _legal_paper_printing
from ..synergy_prototype.ranker import DeckRanker, color_mask


DEFAULT_OUTPUT = Path("frontend/public/synergy-assets")
FEATURE_SCALE = 4096
MAX_ASSET_BYTES = 25 * 1024 * 1024


def _encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _commander_eligible(card):
    if _can_command(card):
        return True
    # Grist's characteristic-defining ability makes it a creature in the command zone.
    first_type = card["type_line"].split(" // ", 1)[0].split("—", 1)[0].casefold().split()
    text = _oracle(card).casefold().replace("’", "'")
    return ("legendary" in first_type and "planeswalker" in first_type
            and bool(re.search(r"as long as [^.]+ isn't on the battlefield, it's [^.]+ creature in addition to its other types", text)))


def _aliases_by_card(catalog, aliases, printings_path=None):
    candidates = defaultdict(set)
    for alias, canonical in aliases.items():
        candidates[alias].add(canonical)
    if printings_path is not None:
        oracle_keys = {row["oracle_id"]: key for key, row in catalog.items()}
        with gzip.open(printings_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                canonical = oracle_keys.get(row.get("oracle_id"))
                faces = row.get("card_faces") or []
                if canonical is None and row.get("layout") == "reversible_card" and len(faces) == 2:
                    left, right = (face.get("oracle_id") for face in faces)
                    if left == right and left in oracle_keys and _legal_paper_printing(row):
                        canonical = oracle_keys[left]
                        candidates[card_key(row["name"])].add(canonical)
                if not canonical or not _legal_paper_printing(row):
                    continue
                for record in (row, *faces):
                    for field in ("flavor_name", "printed_name"):
                        name = record.get(field)
                        if isinstance(name, str) and name.strip():
                            candidates[card_key(name)].add(canonical)
    result = defaultdict(list)
    for alias, choices in sorted(candidates.items()):
        if alias not in catalog and len(choices) == 1:
            result[next(iter(choices))].append(alias)
    return result


def _purchase_offers(quote, limit):
    if not quote:
        return []
    selected = []
    remaining = limit
    # The pricing snapshot already sorts by unit price and deterministic tie breaks.
    for offer in quote.get("offers", [quote]):
        price, stock = offer.get("price_cents"), offer.get("stock")
        if type(price) is not int or price <= 0 or type(stock) is not int or stock <= 0:
            continue
        selected.append({key: offer[key] for key in (
            "price_cents", "stock", "printing", "set_code", "collector_number", "finish",
            "condition", "source_url", "scryfall_id",
        ) if key in offer})
        remaining -= stock
        if remaining <= 0:
            break
    return selected


def _browser_card(key, card, aliases, quotes, index, has_features):
    mask = color_mask(card["color_identity"])
    land = _is_land(card)
    copies = _copy_limit(card)
    produces = (set(card.get("produced_mana", ())) | _land_colors(card, set("WUBRG"))) if land else set(card.get("produced_mana", ()))
    row = {key: card[key] for key in ("name", "type_line", "oracle_text", "mana_cost", "cmc", "keywords", "layout")}
    row.update(oracle_id=card["oracle_id"], color_mask=mask, produced_mask=color_mask(produces),
               produces_colorless="C" in produces, land=land, basic=_is_basic(card),
               copies=copies, can_command=_commander_eligible(card), chooses_color=_chooses_commander_color(card),
               roles=functional_tags(card), offers=_purchase_offers(quotes.get(key), copies))
    if index is not None:
        row["index"] = index
    if not has_features:
        row["has_model_features"] = False
    if land:
        row["land_quality"] = _land_quality(card)
    if card["card_faces"]:
        row["card_faces"] = [{field: face[field] for field in ("name", "type_line", "oracle_text", "mana_cost")}
                             for face in card["card_faces"]]
    for name in ("scryfall_uri", "image_url", "power", "toughness"):
        if card.get(name):
            row[name] = card[name]
    if card.get("game_changer"):
        row["game_changer"] = True
    if aliases.get(key):
        row["aliases"] = aliases[key]
    return row


def _browser_model(ranker):
    model = ranker.model
    return {"feature_count": ranker.features.shape[1], "feature_scale": FEATURE_SCALE,
            "shapes": [list(layer.shape) for layer in model.coefs_],
            "weights": [layer.ravel(order="C").tolist() for layer in model.coefs_],
            "biases": [layer.tolist() for layer in model.intercepts_]}


def export_browser_assets(output, catalog, aliases, ranker, snapshot, *, snapshot_at, printings_path=None):
    """Slice the catalog by color identity and keep all output files below asset limits."""
    if set(catalog) != set(ranker.index):
        raise ValueError("Browser export requires a model trained against the exact Oracle catalog")
    quantized = np.rint(ranker.features * FEATURE_SCALE)
    if not np.isfinite(quantized).all() or np.max(np.abs(quantized)) > 32767:
        raise ValueError("Feature values exceed the Int16 encoding")
    packed = quantized.astype("<i2")
    vectors = {key: packed[index] for key, index in ranker.index.items()}
    return _write_browser_assets(output, catalog, aliases, _browser_model(ranker), vectors, ranker.index,
                                 snapshot, snapshot_at=snapshot_at, model_catalog_at=snapshot_at,
                                 printings_path=printings_path)


def _source_path(directory, name):
    if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("Model reuse requires a plain asset filename")
    return directory / name


def refresh_browser_assets(output, source, catalog, aliases, snapshot, *, snapshot_at, printings_path=None):
    """Refresh legal cards and retail quotes while retaining existing learned feature rows."""
    previous = json.loads((source / "manifest.json").read_bytes())
    if previous.get("schema_version") != 1:
        raise ValueError("Cannot reuse an unsupported browser asset version")
    model = json.loads(_source_path(source, previous["model_path"]).read_bytes())
    width = model["feature_count"]
    by_name, by_identity = {}, {}
    current_identity = {card["oracle_id"]: key for key, card in catalog.items()}
    aliases_by_name = defaultdict(set)
    for alias, key in aliases.items():
        aliases_by_name[alias].add(key)
    for shard in previous["shards"]:
        with gzip.open(_source_path(source, shard["path"]), "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        packed = np.frombuffer(b64decode(payload["features"], validate=True), dtype="<i2")
        if len(packed) != len(payload["cards"]) * width:
            raise ValueError("The existing browser model has an incomplete feature shard")
        for card, vector in zip(payload["cards"], packed.reshape(-1, width), strict=True):
            oracle_id = card.get("oracle_id")
            key = card_key(card["name"])
            canonical = current_identity.get(oracle_id) if oracle_id else key if key in catalog else None
            if canonical is not None:
                for alias in card.get("aliases", []):
                    aliases_by_name[alias].add(canonical)
            if card.get("has_model_features") is False:
                continue
            value = (oracle_id, card.get("index"), vector)
            by_name[key] = value
            if oracle_id:
                by_identity[oracle_id] = value
    vectors, indices = {}, {}
    for key, card in catalog.items():
        result = by_identity.get(card["oracle_id"]) or by_name.get(key)
        if result and result[0] in (None, card["oracle_id"]):
            _, indices[key], vectors[key] = result
    unambiguous = {alias: next(iter(keys)) for alias, keys in aliases_by_name.items() if len(keys) == 1}
    return _write_browser_assets(output, catalog, unambiguous, model, vectors, indices, snapshot,
                                 snapshot_at=snapshot_at, printings_path=printings_path,
                                 model_catalog_at=previous.get("model_catalog_updated_at", previous["oracle_updated_at"]))


def _write_browser_assets(output, catalog, aliases, model, vectors, indices, snapshot, *, snapshot_at,
                          model_catalog_at, printings_path=None):
    aliases_by_card = _aliases_by_card(catalog, aliases, printings_path)
    quotes = snapshot["cards"]
    manifest = {"schema_version": 1, "oracle_updated_at": snapshot_at, "model_path": "model.json",
                "model_catalog_updated_at": model_catalog_at, "oracle_only_cards": len(catalog) - len(vectors),
                "pricing": {key: snapshot[key] for key in ("source", "source_url", "checked_at")}, "shards": []}
    if snapshot.get("methodology"):
        manifest["pricing"]["description"] = snapshot["methodology"]
    by_color = defaultdict(list)
    for key in sorted(catalog):
        row = _browser_card(key, catalog[key], aliases_by_card, quotes, indices.get(key), key in vectors)
        by_color[row["color_mask"]].append(row)

    output.mkdir(parents=True, exist_ok=True)
    revision = hashlib.sha256()
    with TemporaryDirectory(dir=output.parent) as directory:
        staging = Path(directory)
        model_bytes = _encode(model)
        revision.update(model_bytes)
        (staging / manifest["model_path"]).write_bytes(model_bytes)
        placeholder = np.zeros(model["feature_count"], dtype="<i2")
        for mask, cards in sorted(by_color.items()):
            rows = (vectors.get(card_key(card["name"]), placeholder) for card in cards)
            packed = b64encode(b"".join(row.tobytes() for row in rows)).decode("ascii")
            path = f"cards-{mask:02}.json.gz"
            shard = gzip.compress(_encode({"cards": cards, "features": packed}), compresslevel=6, mtime=0)
            revision.update(shard)
            (staging / path).write_bytes(shard)
            manifest["shards"].append({"path": path, "color_mask": mask})
        manifest["revision"] = revision.hexdigest()[:16]
        (staging / "manifest.json").write_bytes(_encode(manifest))
        if any(path.stat().st_size > MAX_ASSET_BYTES for path in staging.iterdir()):
            raise ValueError("A published Commander asset exceeds the 25 MiB file limit")
        for path in [manifest["model_path"], *(entry["path"] for entry in manifest["shards"]), "manifest.json"]:
            (staging / path).replace(output / path)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle-jsonl", type=Path, required=True)
    parser.add_argument("--catalog-updated-at", required=True)
    parser.add_argument("--prices", type=Path, required=True)
    training = parser.add_mutually_exclusive_group(required=True)
    training.add_argument("--previews", type=Path, help="Train a new ranker from collector previews")
    training.add_argument("--reuse-model-from", type=Path, help="Reprice and refresh Oracle data using existing site assets")
    parser.add_argument("--printings-jsonl", type=Path)
    parser.add_argument("--alias-cache", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=2)
    args = parser.parse_args()
    if args.previews is not None and args.epochs < 1:
        parser.error("--epochs must be positive")
    catalog, aliases = load_catalog(args.oracle_jsonl, args.catalog_updated_at, args.alias_cache)
    snapshot = load_snapshot(args.prices)
    options = {"snapshot_at": args.catalog_updated_at, "printings_path": args.printings_jsonl}
    if args.reuse_model_from is not None:
        manifest = refresh_browser_assets(args.output, args.reuse_model_from, catalog, aliases, snapshot, **options)
    else:
        ranker = DeckRanker(catalog, aliases, seed=args.seed)
        train, _, sample = ranker.load_decks(args.previews, tuple(key for key in commander_keys() if key in catalog))
        print(f"Training on {len(train):,} rows from {sample['parsed_decks']:,} parsed decks", flush=True)
        ranker.fit(train, epochs=args.epochs)
        manifest = export_browser_assets(args.output, catalog, aliases, ranker, snapshot, **options)
    print(f"Wrote {len(manifest['shards'])} shards to {args.output} at revision {manifest['revision']}")


if __name__ == "__main__":
    main()
