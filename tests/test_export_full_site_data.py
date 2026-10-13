import json
import importlib.util
from pathlib import Path

import pandas as pd

EXPORT_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "export_full_site_data.py"
)

spec = importlib.util.spec_from_file_location(
    "export_full_site_data",
    EXPORT_SCRIPT_PATH,
)
export_full_site_data_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(export_full_site_data_module)

export_full_site_data = export_full_site_data_module.export_full_site_data
extract_set_code_from_scryfall_uri = (
    export_full_site_data_module.extract_set_code_from_scryfall_uri
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


def test_export_full_site_data_writes_compact_theme_bracket_files(tmp_path):
    processed_dir = tmp_path / "processed" / "2026-06-21"
    output_dir = tmp_path / "site"

    write_json(
        processed_dir / "affinity_rows_with_trends.json",
        [
            {
                "commander_name": "Theme Commander",
                "commander_slug": "theme-commander",
                "tag_name": "Mutate",
                "tag_slug": "mutate",
                "total_decks": 400,
                "tag_decks": 40,
                "tag_affinity_pct": 0.1,
                "z": 2.4,
                "rank_within_tag_by_z": 1,
                "color_identity": ["U", "G"],
            },
            {
                "commander_name": "Theme Commander",
                "commander_slug": "theme-commander",
                "tag_name": "Combo",
                "tag_slug": "combo",
                "total_decks": 400,
                "tag_decks": 30,
                "tag_affinity_pct": 0.075,
                "z": 1.2,
            },
            {
                "commander_name": "Below Threshold",
                "commander_slug": "below-threshold",
                "tag_name": "Mutate",
                "tag_slug": "mutate",
                "total_decks": 300,
                "tag_decks": 8,
                "tag_affinity_pct": 0.026,
                "z": 1.04,
            },
            {
                "commander_name": "Below Threshold",
                "commander_slug": "below-threshold",
                "tag_name": "Combo",
                "tag_slug": "combo",
                "total_decks": 300,
                "tag_decks": 6,
                "tag_affinity_pct": 0.02,
                "z": 0.4,
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

    assert manifest["theme_bracket_export"]["minimum_z"] == 1.05

    theme_index = read_json(output_dir / "theme-brackets" / "index.json")
    mutate_info = next(
        theme for theme in theme_index if theme["tag_slug"] == "mutate"
    )
    combo_info = next(
        theme for theme in theme_index if theme["tag_slug"] == "combo"
    )
    assert mutate_info["qualified_commander_count"] == 1
    assert mutate_info["minimum_z"] == 1.05
    assert mutate_info["qualification_rule"] == "theme_z_and_bracket_rules"
    assert combo_info["qualified_commander_count"] == 2
    assert combo_info["minimum_z"] is None
    assert combo_info["qualification_rule"] == "bracket_rules_only"

    mutate_rows = read_json(output_dir / "theme-brackets" / "mutate.json")
    assert len(mutate_rows) == 1
    assert mutate_rows[0]["commander_slug"] == "theme-commander"
    assert mutate_rows[0]["theme_z"] == 2.4
    assert mutate_rows[0]["theme_tag_decks"] == 40
    assert mutate_rows[0]["bracket_tag_rows"] == [
        {"tag_name": "Combo", "tag_slug": "combo", "z": 1.2, "tag_decks": 30}
    ]

    combo_rows = read_json(output_dir / "theme-brackets" / "combo.json")
    assert {row["commander_slug"] for row in combo_rows} == {
        "theme-commander",
        "below-threshold",
    }

    report = read_json(output_dir / "theme-report.json")
    assert manifest["theme_report_export"] == {
        "file": "theme-report.json",
        "theme_count": 2,
        "commander_count": 2,
        "row_count": 3,
        "minimum_z": 1.05,
    }
    mutate_report = next(theme for theme in report["themes"] if theme["tag_slug"] == "mutate")
    assert mutate_report["rows"] == [
        {
            "commander_slug": "theme-commander",
            "theme_z": 2.4,
            "theme_tag_decks": 40,
            "theme_affinity_pct": 0.1,
        }
    ]


def test_theme_report_matches_theme_brackets_without_deck_minimums(tmp_path):
    tiny_commander = {
        "commander_slug": "tiny-commander",
        "commander_name": "Tiny Commander",
        "total_decks": 10,
        "color_identity": ["U"],
        "tag_decks": 1,
        "tag_affinity_pct": 0.1,
    }
    rows = [
        {**tiny_commander, "tag_slug": "artifacts", "tag_name": "Artifacts", "z": 1.05},
        {**tiny_commander, "tag_slug": "artifacts", "tag_name": "Artifacts", "z": 1.06},
        {
            **tiny_commander,
            "commander_slug": "boundary-commander",
            "tag_slug": "artifacts",
            "tag_name": "Artifacts",
            "z": 1.05,
        },
        {**tiny_commander, "tag_slug": "snow", "tag_name": "Snow", "z": 1.04},
    ]

    for slug in ["cedh", "aggro", "control", "midrange", "tempo", "combo"]:
        rows.extend(
            [
                {**tiny_commander, "tag_slug": slug, "tag_name": slug.title(), "z": -2.0},
                {
                    **tiny_commander,
                    "commander_slug": "null-signal-commander",
                    "commander_name": "Null Signal Commander",
                    "tag_slug": slug,
                    "tag_name": slug.title(),
                    "z": None,
                },
            ]
        )

    df = pd.DataFrame(rows)
    export_full_site_data_module.export_theme_bracket_files(df, None, tmp_path)
    metadata = export_full_site_data_module.export_theme_report(df, None, tmp_path)
    report = read_json(tmp_path / "theme-report.json")
    commanders = {row["commander_slug"]: row for row in report["commanders"]}

    assert metadata["row_count"] == 14
    assert metadata["theme_count"] == 8
    assert metadata["commander_count"] == 3
    assert commanders["tiny-commander"]["total_decks"] == 10
    assert len(commanders["tiny-commander"]["bracket_tag_rows"]) == 6
    assert {theme["tag_slug"] for theme in report["themes"]} == {
        "artifacts", "snow", "cedh", "aggro", "control", "midrange", "tempo", "combo"
    }

    for theme in report["themes"]:
        bracket_rows = read_json(tmp_path / "theme-brackets" / f'{theme["tag_slug"]}.json')
        assert len(theme["rows"]) == len(bracket_rows)

        for report_row, bracket_row in zip(theme["rows"], bracket_rows):
            commander = commanders[report_row["commander_slug"]]
            assert report_row == {key: bracket_row[key] for key in report_row}
            assert commander == {key: bracket_row[key] for key in commander}

    artifacts = next(theme for theme in report["themes"] if theme["tag_slug"] == "artifacts")
    assert [row["theme_z"] for row in artifacts["rows"]] == [1.06, 1.05]
    assert all(row["theme_tag_decks"] == 1 for row in artifacts["rows"])
    assert next(theme for theme in report["themes"] if theme["tag_slug"] == "snow")["rows"] == []


def test_theme_report_retains_empty_themes_and_handles_an_empty_dataset(tmp_path):
    df = pd.DataFrame(
        [
            {
                "commander_slug": "below-threshold",
                "tag_slug": "snow",
                "tag_name": "Snow",
                "z": 1.04,
            }
        ]
    )
    metadata = export_full_site_data_module.export_theme_report(df, None, tmp_path)
    assert metadata["row_count"] == 0
    assert read_json(tmp_path / "theme-report.json") == {
        "commanders": [],
        "themes": [{"tag_slug": "snow", "tag_name": "Snow", "rows": []}],
    }

    metadata = export_full_site_data_module.export_theme_report(df.iloc[:0], None, tmp_path)
    assert metadata["row_count"] == metadata["commander_count"] == metadata["theme_count"] == 0
    assert read_json(tmp_path / "theme-report.json") == {"commanders": [], "themes": []}


def test_theme_report_writes_strict_json_for_nonfinite_values(tmp_path):
    df = pd.DataFrame(
        [
            {
                "commander_slug": "commander",
                "tag_slug": "combo",
                "tag_name": "Combo",
                "total_decks": float("nan"),
                "tag_decks": float("inf"),
                "tag_affinity_pct": float("inf"),
                "z": float("-inf"),
            }
        ]
    )
    export_full_site_data_module.export_theme_report(df, None, tmp_path)

    def reject_nonfinite(value):
        raise AssertionError(f"Nonfinite JSON number: {value}")

    report = json.loads(
        (tmp_path / "theme-report.json").read_text(encoding="utf-8"),
        parse_constant=reject_nonfinite,
    )
    assert report["commanders"][0]["total_decks"] is None
    assert report["commanders"][0]["bracket_tag_rows"][0]["z"] is None
    assert report["themes"][0]["rows"][0] == {
        "commander_slug": "commander",
        "theme_z": None,
        "theme_tag_decks": None,
        "theme_affinity_pct": None,
    }


def test_export_recovers_cached_faces_for_every_commander_surface(tmp_path):
    processed_dir = tmp_path / "processed"
    output_dir = tmp_path / "site"
    cards = [
        {
            "name": "Front // Back",
            "scryfall_uri": "https://scryfall.com/card/tst/1/front",
            "card_faces": [
                {"name": "Front", "image_uris": {"normal": "front.jpg"}},
                {"name": "Back", "image_uris": {"normal": "back.jpg"}},
            ],
        },
        {
            "name": "Background",
            "scryfall_uri": "https://scryfall.com/card/tst/2/background",
            "image_uris": {"normal": "background.jpg"},
        },
    ]
    write_json(
        processed_dir / "scryfall_lookup_cache.json",
        {"exact:front": cards[0], "exact:background": cards[1]},
    )
    write_json(
        processed_dir / "affinity_rows_with_trends.json",
        [{
            "commander_slug": "front-background",
            "commander_name": "Front // Background",
            "scryfall_card_names": ["Front // Back", "Background"],
            "card_image_url": "front.jpg",
            "partner_card_image_urls": ["front.jpg", "background.jpg"],
            "scryfall_uri": cards[0]["scryfall_uri"],
            "color_identity": ["U", "G"],
            "tag_slug": "snow",
            "tag_name": "Snow",
            "z": 2.4,
            "tag_decks": 3,
            "total_decks": 10,
        }],
    )

    write_json(processed_dir / "analysis_summary.json", {})
    write_json(processed_dir / "trend_summary.json", {})
    write_json(processed_dir / "tag_summary.json", [])
    export_full_site_data(
        processed_dir=processed_dir, output_dir=output_dir, page_size=10, clean_output=False
    )

    expected = [
        {
            "name": "Front // Back",
            "scryfall_uri": cards[0]["scryfall_uri"],
            "faces": [
                {"name": "Front", "image_url": "front.jpg"},
                {"name": "Back", "image_url": "back.jpg"},
            ],
        },
        {
            "name": "Background",
            "scryfall_uri": cards[1]["scryfall_uri"],
            "faces": [{"name": "Background", "image_url": "background.jpg"}],
        },
    ]
    files = [
        "commanders/index.json", "commanders/front-background.json", "sets/tst.json",
        "tags/snow.json", "theme-brackets/snow.json", "leaderboard/page_0001.json",
    ]
    for filename in files:
        row = read_json(output_dir / filename)[0]
        assert row["commander_cards"] == expected, filename
        assert row["color_identity"] == ["U", "G"], filename

    report_row = read_json(output_dir / "theme-report.json")["commanders"][0]
    assert report_row["commander_cards"] == expected
    assert report_row["partner_card_image_urls"] == ["front.jpg", "background.jpg"]


def test_incomplete_cache_leaves_legacy_metadata_for_frontend_fallback(tmp_path):
    cache_path = tmp_path / "cache.json"
    write_json(cache_path, {"exact:partner": {"name": "Partner", "image_uris": {"normal": "p.jpg"}}})
    df = pd.DataFrame([{
        "commander_slug": "pair",
        "commander_name": "Partner // Background",
        "scryfall_card_names": ["Partner", "Background"],
        "partner_card_image_urls": ["p.jpg", "b.jpg"],
    }])
    upgraded = export_full_site_data_module.ensure_commander_card_metadata(df, cache_path)
    assert "commander_cards" not in upgraded.columns
    assert upgraded.iloc[0]["partner_card_image_urls"] == ["p.jpg", "b.jpg"]


def test_meld_cache_migration_waits_for_result_and_keeps_one_commander(tmp_path):
    cache_path = tmp_path / "cache.json"
    card = {
        "name": "Bruna, the Fading Light",
        "layout": "meld",
        "image_uris": {"normal": "bruna-front.jpg"},
        "all_parts": [{
            "name": "Brisela, Voice of Nightmares",
            "component": "meld_result",
            "uri": "https://api.scryfall.com/cards/brisela",
        }],
    }
    cache = {"exact:bruna, the fading light": card}
    df = pd.DataFrame([{
        "commander_slug": "bruna",
        "commander_name": card["name"],
        "scryfall_card_names": [card["name"]],
        "card_image_url": "bruna-front.jpg",
    }])
    write_json(cache_path, cache)
    upgraded = export_full_site_data_module.ensure_commander_card_metadata(df, cache_path)
    assert "commander_cards" not in upgraded.columns

    cache["uri:https://api.scryfall.com/cards/brisela"] = {
        "name": "Brisela, Voice of Nightmares",
        "image_uris": {"normal": "brisela-back.jpg"},
    }
    write_json(cache_path, cache)
    upgraded = export_full_site_data_module.ensure_commander_card_metadata(df, cache_path)
    previews = upgraded.iloc[0]["commander_cards"]
    assert len(previews) == 1
    assert previews[0]["name"] == card["name"]
    assert [face["image_url"] for face in previews[0]["faces"]] == [
        "bruna-front.jpg", "brisela-back.jpg"
    ]


def test_partial_partner_metadata_recovers_both_cards_and_color_union(tmp_path):
    cache_path = tmp_path / "cache.json"
    first = {"name": "First", "color_identity": ["U", "R"], "image_uris": {"normal": "first.jpg"}}
    second = {"name": "Second", "color_identity": ["W", "R"], "image_uris": {"normal": "second.jpg"}}
    write_json(cache_path, {"exact:first": first, "exact:second": second})
    # The old early-return lookup can retain only the SECOND member of a pair.
    df = pd.DataFrame([{
        "commander_slug": "pair",
        "commander_name": "First // Second (Commander)",
        "scryfall_card_names": ["Second"],
        "partner_card_image_urls": ["second.jpg"],
        "color_identity": ["W", "R"],
    }])
    upgraded = export_full_site_data_module.ensure_commander_card_metadata(df, cache_path)
    previews = upgraded.iloc[0]["commander_cards"]
    assert [card["name"] for card in previews] == ["First", "Second"]
    assert [card["faces"][0]["image_url"] for card in previews] == ["first.jpg", "second.jpg"]
    assert upgraded.iloc[0]["color_identity"] == ["W", "U", "R"]


def test_partial_partner_colors_recover_from_standalone_rows_without_cache(tmp_path):
    df = pd.DataFrame([
        {
            "commander_slug": "pair",
            "commander_name": "First // Second (Commander)",
            "scryfall_card_names": ["First"],
            "color_identity": ["U", "R"],
        },
        {"commander_slug": "first", "commander_name": "First (Commander)", "color_identity": ["U", "R"]},
        {"commander_slug": "second", "commander_name": "Second (Commander)", "color_identity": ["W", "R"]},
    ])
    upgraded = export_full_site_data_module.ensure_commander_card_metadata(df, tmp_path / "missing.json")
    assert upgraded.iloc[0]["color_identity"] == ["W", "U", "R"]
    assert "commander_cards" not in upgraded.columns

    # If a member is unknown, keep the legacy data incomplete for runtime lookup.
    unknown_member = df.iloc[:2]
    unchanged = export_full_site_data_module.ensure_commander_card_metadata(unknown_member, tmp_path / "missing.json")
    assert unchanged.iloc[0]["color_identity"] == ["U", "R"]
    assert "commander_cards" not in unchanged.columns
