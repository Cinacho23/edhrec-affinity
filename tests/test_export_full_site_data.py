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


def read_theme_rows(path: Path):
    data = read_json(path)
    assert data["qualification_score_field"] == "z"
    assert isinstance(data["rows"], list)
    return data["rows"]


def test_extract_set_code_from_scryfall_uri():
    uri = "https://scryfall.com/card/khm/179/jorn-god-of-winter"

    assert extract_set_code_from_scryfall_uri(uri) == "khm"


def test_upgrade_exports_current_theme_qualification_and_preserves_legacy_history(tmp_path):
    processed_dir = tmp_path / "processed"
    output_dir = tmp_path / "site"
    common = {
        "commander_name": "Test Commander", "commander_slug": "test",
        "total_decks": 1000, "tag_decks": 200, "tag_affinity_pct": 0.2,
        "affinity_model_version": "beta_binomial_v1", "affinity_model_status": "fitted",
        "tag_affinity_adjusted_pct": 0.19, "tag_affinity_lower_pct": 0.17,
        "tag_affinity_upper_pct": 0.22,
        "rank_within_tag_by_z": 2,
        "scryfall_uri": "https://scryfall.com/card/khm/1/test",
    }
    write_json(processed_dir / "affinity_rows_with_trends.json", [
        {**common, "tag_name": "Tokens", "tag_slug": "tokens", "z": 3, "legacy_z": .2},
        {**common, "tag_name": "Combo", "tag_slug": "combo", "z": 1.05, "legacy_z": -2,
         "tag_decks": 30, "tag_affinity_pct": .03, "tag_affinity_adjusted_pct": .029,
         "tag_affinity_lower_pct": .02, "tag_affinity_upper_pct": .04, "rank_within_tag_by_z": 9},
        {**common, "tag_name": "Snow", "tag_slug": "snow", "z": 1.04, "legacy_z": 5},
        {**common, "tag_name": "Lands", "tag_slug": "lands", "z": None, "legacy_z": 5},
    ])
    write_json(processed_dir / "analysis_summary.json", {})
    write_json(processed_dir / "trend_summary.json", {})
    write_json(processed_dir / "tag_summary.json", [])
    manifest = export_full_site_data(processed_dir, output_dir, 10, True)
    assert manifest["affinity_model_version"] == "beta_binomial_v1"
    set_rows = read_json(output_dir / "sets" / "khm.json")
    tokens = next(row for row in set_rows if row["tag_slug"] == "tokens")
    assert tokens["tag_affinity_adjusted_pct"] == 0.19
    assert tokens["legacy_z"] == .2
    theme_rows = read_theme_rows(output_dir / "theme-brackets" / "tokens.json")
    assert len(theme_rows) == 1
    assert theme_rows[0]["theme_z"] == 3
    assert theme_rows[0]["theme_affinity_z"] == 3
    assert theme_rows[0]["theme_legacy_z"] == .2
    assert theme_rows[0]["theme_affinity_pct"] == .2
    assert theme_rows[0]["theme_affinity_adjusted_pct"] == .19
    assert theme_rows[0]["theme_affinity_lower_pct"] == .17
    assert theme_rows[0]["theme_affinity_upper_pct"] == .22
    assert theme_rows[0]["theme_affinity_model_status"] == "fitted"
    assert theme_rows[0]["theme_affinity_model_version"] == "beta_binomial_v1"
    assert theme_rows[0]["theme_rank_within_tag_by_z"] == 2
    assert theme_rows[0]["bracket_tag_rows"] == [{
        "tag_name": "Combo", "tag_slug": "combo", "z": 1.05, "legacy_z": -2,
        "tag_decks": 30, "tag_affinity_pct": .03, "tag_affinity_adjusted_pct": .029,
        "tag_affinity_lower_pct": .02, "tag_affinity_upper_pct": .04,
        "affinity_model_status": "fitted", "affinity_model_version": "beta_binomial_v1",
        "rank_within_tag_by_z": 9,
    }]
    assert read_theme_rows(output_dir / "theme-brackets" / "snow.json") == []
    assert read_theme_rows(output_dir / "theme-brackets" / "lands.json") == []
    report = read_json(output_dir / "theme-report.json")
    token_rows = next(theme for theme in report["themes"] if theme["tag_slug"] == "tokens")["rows"]
    assert report["qualification_score_field"] == "z"
    assert token_rows[0]["theme_z"] == 3
    assert token_rows[0]["theme_affinity_z"] == 3
    assert token_rows[0]["theme_legacy_z"] == .2
    assert token_rows[0] == {key: theme_rows[0][key] for key in token_rows[0]}
    assert report["commanders"][0]["bracket_tag_rows"] == theme_rows[0]["bracket_tag_rows"]


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
    assert manifest["theme_bracket_export"]["qualification_score_field"] == "z"

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
    assert mutate_info["qualification_score_field"] == "z"
    assert combo_info["qualified_commander_count"] == 2
    assert combo_info["minimum_z"] is None
    assert combo_info["qualification_rule"] == "bracket_rules_only"
    assert combo_info["qualification_score_field"] == "z"

    mutate_rows = read_theme_rows(output_dir / "theme-brackets" / "mutate.json")
    assert len(mutate_rows) == 1
    assert mutate_rows[0]["commander_slug"] == "theme-commander"
    assert mutate_rows[0]["theme_z"] == 2.4
    assert mutate_rows[0]["theme_tag_decks"] == 40
    assert mutate_rows[0]["bracket_tag_rows"] == [
        {"tag_name": "Combo", "tag_slug": "combo", "z": 1.2, "tag_decks": 30,
         "tag_affinity_pct": .075, "tag_affinity_adjusted_pct": None,
         "tag_affinity_lower_pct": None, "tag_affinity_upper_pct": None,
         "affinity_model_status": None, "affinity_model_version": None,
         "rank_within_tag_by_z": None}
    ]

    combo_rows = read_theme_rows(output_dir / "theme-brackets" / "combo.json")
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
        "qualification_score_field": "z",
    }
    mutate_report = next(theme for theme in report["themes"] if theme["tag_slug"] == "mutate")
    assert mutate_report["rows"] == [
        {
            "commander_slug": "theme-commander",
            "theme_z": 2.4,
            "theme_affinity_z": 2.4,
            "theme_tag_decks": 40,
            "theme_affinity_pct": 0.1,
            "theme_affinity_adjusted_pct": None,
            "theme_affinity_lower_pct": None,
            "theme_affinity_upper_pct": None,
            "theme_affinity_model_status": None,
            "theme_affinity_model_version": None,
            "theme_rank_within_tag_by_z": 1,
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
        bracket_rows = read_theme_rows(tmp_path / "theme-brackets" / f'{theme["tag_slug"]}.json')
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
        "qualification_score_field": "z",
    }

    metadata = export_full_site_data_module.export_theme_report(df.iloc[:0], None, tmp_path)
    assert metadata["row_count"] == metadata["commander_count"] == metadata["theme_count"] == 0
    assert read_json(tmp_path / "theme-report.json") == {
        "commanders": [], "themes": [], "qualification_score_field": "z"
    }


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
        "theme_affinity_z": None,
        "theme_tag_decks": None,
        "theme_affinity_pct": None,
        "theme_affinity_adjusted_pct": None,
        "theme_affinity_lower_pct": None,
        "theme_affinity_upper_pct": None,
        "theme_affinity_model_status": None,
        "theme_affinity_model_version": None,
        "theme_rank_within_tag_by_z": None,
    }


def test_compact_metrics_preserve_explicit_missing_score_and_signal_source_identity(tmp_path):
    common = {"commander_slug": "shared", "commander_name": "Shared", "total_decks": 1000}
    rows = [
        {**common, "tag_slug": "tokens", "tag_name": "Tokens", "tag_decks": 200,
         "tag_affinity_pct": .2, "tag_affinity_adjusted_pct": .18,
         "tag_affinity_lower_pct": .15, "tag_affinity_upper_pct": .21,
         "z": 3, "legacy_z": 1.2, "rank_within_tag_by_z": 7,
         "affinity_model_status": "fitted", "affinity_model_version": "beta_binomial_v1"},
        {**common, "tag_slug": "snow", "tag_name": "Snow", "tag_decks": 100,
         "tag_affinity_pct": .1, "tag_affinity_adjusted_pct": .09,
         "tag_affinity_lower_pct": .08, "tag_affinity_upper_pct": .11,
         "z": 88, "legacy_z": 1.3, "rank_within_tag_by_z": 1,
         "affinity_model_status": "fitted", "affinity_model_version": "beta_binomial_v1"},
        {**common, "tag_slug": "lands", "tag_name": "Lands", "tag_decks": 400,
         "tag_affinity_pct": .4, "z": None, "legacy_z": 1.4},
        {**common, "tag_slug": "cedh", "tag_name": "cEDH", "tag_decks": 30,
         "tag_affinity_pct": .03, "tag_affinity_adjusted_pct": .025,
         "tag_affinity_lower_pct": .01, "tag_affinity_upper_pct": .04,
         "z": -.2, "legacy_z": .4, "rank_within_tag_by_z": 19,
         "affinity_model_status": "fitted", "affinity_model_version": "beta_binomial_v1"},
    ]
    df = pd.DataFrame(rows)
    export_full_site_data_module.export_theme_bracket_files(df, None, tmp_path)
    export_full_site_data_module.export_theme_report(df, None, tmp_path)
    report = read_json(tmp_path / "theme-report.json")
    mappings = {
        "theme_affinity_pct": "tag_affinity_pct",
        "theme_affinity_adjusted_pct": "tag_affinity_adjusted_pct",
        "theme_affinity_lower_pct": "tag_affinity_lower_pct",
        "theme_affinity_upper_pct": "tag_affinity_upper_pct",
        "theme_affinity_model_status": "affinity_model_status",
        "theme_affinity_model_version": "affinity_model_version",
        "theme_rank_within_tag_by_z": "rank_within_tag_by_z",
        "theme_affinity_z": "z",
        "theme_z": "z",
        "theme_legacy_z": "legacy_z",
    }
    for canonical in rows:
        if canonical["tag_slug"] == "lands":
            continue
        compact = read_theme_rows(tmp_path / "theme-brackets" / f'{canonical["tag_slug"]}.json')[0]
        report_row = next(theme for theme in report["themes"] if theme["tag_slug"] == canonical["tag_slug"])["rows"][0]
        for destination, source in mappings.items():
            assert compact[destination] == canonical.get(source)
            assert report_row[destination] == canonical.get(source)
        signal = compact["bracket_tag_rows"][0]
        assert signal["tag_slug"] == "cedh"
        for source in ["tag_affinity_pct", "tag_affinity_adjusted_pct", "tag_affinity_lower_pct",
                       "tag_affinity_upper_pct", "affinity_model_status", "affinity_model_version",
                       "rank_within_tag_by_z", "z", "legacy_z"]:
            assert signal[source] == rows[-1][source]
    assert read_theme_rows(tmp_path / "theme-brackets/lands.json") == []
    assert next(theme for theme in report["themes"] if theme["tag_slug"] == "lands")["rows"] == []


def test_current_score_order_and_ties_match_sets_commanders_and_compact_signals(tmp_path):
    processed_dir = tmp_path / "processed"
    output_dir = tmp_path / "site"
    common = {
        "commander_slug": "shared", "commander_name": "Shared", "total_decks": 1000,
        "tag_decks": 10, "tag_affinity_pct": .01,
        "scryfall_uri": "https://scryfall.com/card/khm/1/shared",
    }
    rows = [
        {**common, "tag_slug": "tempo", "tag_name": "Tempo", "z": 1, "legacy_z": 100},
        {**common, "tag_slug": "combo", "tag_name": "Combo", "z": 2, "legacy_z": 99},
        {**common, "tag_slug": "aggro", "tag_name": "Aggro", "z": 2, "legacy_z": .01},
        {**common, "tag_slug": "cedh", "tag_name": "cEDH", "z": None, "legacy_z": 5},
        {**common, "tag_slug": "tokens", "tag_name": "Tokens", "z": 2, "legacy_z": 9},
        {**common, "tag_slug": "tokens", "tag_name": "Tokens", "z": 3, "legacy_z": 1},
    ]
    write_json(processed_dir / "affinity_rows_with_trends.json", rows)
    write_json(processed_dir / "analysis_summary.json", {})
    write_json(processed_dir / "trend_summary.json", {})
    write_json(processed_dir / "tag_summary.json", [])
    export_full_site_data(processed_dir, output_dir, 10, True)

    def signal_order(records):
        return [row["tag_slug"] for row in records if row["tag_slug"] in {"cedh", "aggro", "combo", "tempo"}]

    expected = ["aggro", "combo", "tempo", "cedh"]
    assert signal_order(read_json(output_dir / "commanders/shared.json")) == expected
    assert signal_order(read_json(output_dir / "sets/khm.json")) == expected
    theme = read_theme_rows(output_dir / "theme-brackets/tokens.json")[0]
    assert signal_order(theme["bracket_tag_rows"]) == expected
    assert theme["theme_z"] == theme["theme_affinity_z"] == 3
    assert theme["theme_legacy_z"] == 1
    report = read_json(output_dir / "theme-report.json")
    assert signal_order(report["commanders"][0]["bracket_tag_rows"]) == expected
    theme_report = next(theme for theme in report["themes"] if theme["tag_slug"] == "tokens")["rows"][0]
    assert theme_report["theme_z"] == 3
