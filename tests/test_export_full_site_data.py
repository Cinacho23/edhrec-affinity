import json
from pathlib import Path

from scripts.export_full_site_data import (
    export_full_site_data,
    extract_set_code_from_scryfall_uri,
)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_extract_set_code_from_scryfall_uri():
    uri = "https://scryfall.com/card/khm/179/jorn-god-of-winter"

    assert extract_set_code_from_scryfall_uri(uri) == "khm"


def test_export_full_site_data_writes_set_files(tmp_path):
    processed_dir = tmp_path / "processed" / "2026-06-21"
    output_dir = tmp_path / "site"

    write_json(
        processed_dir / "affinity_rows_with_trends.json",
        [
            {
                "commander_name": "Jorn, God of Winter",
                "commander_slug": "jorn-god-of-winter",
                "tag_name": "Snow",
                "tag_slug": "snow",
                "total_decks": 300,
                "tag_decks": 42,
                "tag_affinity_pct": 0.14,
                "z": 3.2,
                "rank_within_tag_by_z": 1,
                "color_identity": ["U", "B", "G"],
                "scryfall_uri": "https://scryfall.com/card/khm/179/jorn-god-of-winter",
                "origin_sets": [
                    {
                        "set_code": "khm",
                        "set_name": "Kaldheim",
                        "released_at": "2021-02-05",
                        "scryfall_set_uri": "https://scryfall.com/sets/khm",
                    }
                ],
            },
            {
                "commander_name": "Fallback Commander",
                "commander_slug": "fallback-commander",
                "tag_name": "Artifacts",
                "tag_slug": "artifacts",
                "total_decks": 200,
                "tag_decks": 12,
                "tag_affinity_pct": 0.06,
                "z": 1.4,
                "rank_within_tag_by_z": 5,
                "color_identity": ["U"],
                "scryfall_uri": "https://scryfall.com/card/cmm/99/fallback-commander",
            },
            {
                "commander_name": "Jorn, God of Winter",
                "commander_slug": "jorn-god-of-winter",
                "tag_name": "Sultai",
                "tag_slug": "sultai",
                "total_decks": 300,
                "tag_decks": 20,
                "tag_affinity_pct": 0.07,
                "z": 1.9,
                "rank_within_tag_by_z": 8,
                "color_identity": ["U", "B", "G"],
                "scryfall_uri": "https://scryfall.com/card/khm/179/jorn-god-of-winter",
                "origin_sets": [
                    {
                        "set_code": "khm",
                        "set_name": "Kaldheim",
                        "released_at": "2021-02-05",
                        "scryfall_set_uri": "https://scryfall.com/sets/khm",
                    }
                ],
            },
        ],
    )
    write_json(processed_dir / "analysis_summary.json", {"ok": True})
    write_json(processed_dir / "trend_summary.json", {"ok": True})
    write_json(processed_dir / "tag_summary.json", [])

    manifest = export_full_site_data(
        processed_dir=processed_dir,
        output_dir=output_dir,
        page_size=10,
        clean_output=True,
    )

    assert manifest["set_export"]["set_count"] == 2

    set_index = read_json(output_dir / "sets" / "index.json")
    assert {set_info["set_code"] for set_info in set_index} == {"khm", "cmm"}
    khm_index = next(set_info for set_info in set_index if set_info["set_code"] == "khm")
    assert khm_index["commander_count"] == 1
    assert khm_index["row_count"] == 2

    kaldheim_rows = read_json(output_dir / "sets" / "khm.json")
    assert kaldheim_rows[0]["commander_slug"] == "jorn-god-of-winter"
    assert kaldheim_rows[0]["color_identity"] == ["U", "B", "G"]
    assert kaldheim_rows[0]["origin_set_name"] == "Kaldheim"
    assert kaldheim_rows[0]["tag_name"] == "Snow"
    assert kaldheim_rows[0]["z"] == 3.2
    assert kaldheim_rows[1]["tag_name"] == "Sultai"

    commander_index = read_json(output_dir / "commanders" / "index.json")
    jorn = next(
        commander
        for commander in commander_index
        if commander["commander_slug"] == "jorn-god-of-winter"
    )
    assert jorn["origin_set_code"] == "khm"

    fallback_rows = read_json(output_dir / "sets" / "cmm.json")
    assert fallback_rows[0]["origin_set_name"] == "CMM"
