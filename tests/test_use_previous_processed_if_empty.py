import json
import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "use_previous_processed_if_empty.py"
)

spec = importlib.util.spec_from_file_location(
    "use_previous_processed_if_empty",
    SCRIPT_PATH,
)
fallback_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(fallback_module)

COMMANDER_TAGS_CLEAN_FILENAME = fallback_module.COMMANDER_TAGS_CLEAN_FILENAME
FALLBACK_REPORT_FILENAME = fallback_module.FALLBACK_REPORT_FILENAME
use_previous_processed_if_empty = fallback_module.use_previous_processed_if_empty


def write_clean_tags(snapshot_dir: Path, rows: list[dict]) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / COMMANDER_TAGS_CLEAN_FILENAME
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def clean_row(slug: str) -> dict:
    return {
        "commander_name": slug.replace("-", " ").title(),
        "commander_slug": slug,
        "total_decks": 300,
        "tag_name": "Tokens",
        "tag_slug": "tokens",
        "tag_decks": 25,
        "source_type": "commander_json",
        "scrape_timestamp": "2026-07-01T00:00:00+00:00",
    }


def test_non_empty_current_snapshot_does_not_fallback(tmp_path: Path) -> None:
    processed_root = tmp_path / "processed"
    current_dir = processed_root / "2026-07-06"

    write_clean_tags(current_dir, [clean_row("current-commander")])

    summary = use_previous_processed_if_empty(
        current_dir=current_dir,
        processed_root=processed_root,
    )

    assert summary["fallback_used"] is False
    assert summary["current_clean_tag_row_count"] == 1
    assert read_json(current_dir / COMMANDER_TAGS_CLEAN_FILENAME) == [
        clean_row("current-commander")
    ]
    assert not (current_dir / FALLBACK_REPORT_FILENAME).exists()


def test_empty_current_snapshot_uses_latest_previous_non_empty_snapshot(
    tmp_path: Path,
) -> None:
    processed_root = tmp_path / "processed"
    old_dir = processed_root / "2026-06-23"
    latest_good_dir = processed_root / "2026-06-30"
    empty_previous_dir = processed_root / "2026-07-01"
    current_dir = processed_root / "2026-07-06"

    write_clean_tags(old_dir, [clean_row("old-commander")])
    write_clean_tags(latest_good_dir, [clean_row("latest-good-commander")])
    write_clean_tags(empty_previous_dir, [])
    write_clean_tags(current_dir, [])

    summary = use_previous_processed_if_empty(
        current_dir=current_dir,
        processed_root=processed_root,
    )

    assert summary["fallback_used"] is True
    assert summary["fallback_source_dir"] == str(latest_good_dir)
    assert summary["fallback_clean_tag_row_count"] == 1
    assert read_json(current_dir / COMMANDER_TAGS_CLEAN_FILENAME) == [
        clean_row("latest-good-commander")
    ]

    fallback_report = read_json(current_dir / FALLBACK_REPORT_FILENAME)
    assert fallback_report["fallback_used"] is True
    assert fallback_report["reason"] == "current_clean_tag_row_count_is_zero"


def test_empty_current_snapshot_without_fallback_raises_clear_error(
    tmp_path: Path,
) -> None:
    processed_root = tmp_path / "processed"
    current_dir = processed_root / "2026-07-06"

    write_clean_tags(current_dir, [])

    with pytest.raises(RuntimeError, match="no older non-empty processed snapshot"):
        use_previous_processed_if_empty(
            current_dir=current_dir,
            processed_root=processed_root,
        )
