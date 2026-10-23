"""Source eligibility is enforced before expensive card/deck collection."""

import importlib

import pytest

from edhrec_affinity.build_styles.__main__ import analyze_evidence, collect_evidence, parser
from edhrec_affinity.build_styles.eligibility import (
    MIN_COMMANDER_DECKS, CommanderBelowMinimum, commander_is_eligible,
    require_eligible_commander, valid_deck_count, validate_minimum,
)


@pytest.mark.parametrize("count,eligible", [(0, False), (199, False), (200, True), (201, True), ("1,000", True)])
def test_discovery_minimum_is_200_inclusive(count, eligible):
    assert MIN_COMMANDER_DECKS == 200
    assert commander_is_eligible(count) is eligible


@pytest.mark.parametrize("count", [True, False, None, -1, 199.9, 200.1, float("nan"), float("inf"), "unknown"])
def test_unknown_or_non_whole_source_counts_do_not_establish_eligibility(count):
    assert valid_deck_count(count) is None
    assert not commander_is_eligible(count)
    with pytest.raises(ValueError):
        require_eligible_commander(count)


def test_source_cli_cannot_lower_the_minimum():
    assert parser().parse_args(["--commander", "ghave"]).min_total_decks == 200
    assert parser().parse_args(["--commander", "ghave", "--min-total-decks", "250"]).min_total_decks == 250
    for value in ("0", "199", "199.9", "nan"):
        with pytest.raises(SystemExit):
            parser().parse_args(["--commander", "ghave", "--min-total-decks", value])
    with pytest.raises(ValueError, match="at least 200"):
        validate_minimum(0)


def install_base_only_cache(monkeypatch, count):
    calls = []
    closed = []

    class BaseOnlyCache:
        def __init__(self, *args, **kwargs):
            self.blocked = False
            self.provenance = []

        def get(self, relative, url, **kwargs):
            assert relative == "base.json", "No profiles, deck index or previews should be read here"
            calls.append(relative)
            return {"container": {"json_dict": {"card": {
                "name": "Ghave", "names": ["Ghave"], "num_decks": count, "url": "/commanders/ghave",
            }, "cardlists": []}}}

        def close(self):
            closed.append(True)

    module = importlib.import_module("edhrec_affinity.build_styles.__main__")
    monkeypatch.setattr(module, "Cache", BaseOnlyCache)
    return module, calls, closed


def test_current_source_count_199_stops_before_profiles_decks_metadata_or_report(monkeypatch, tmp_path):
    module, calls, closed = install_base_only_cache(monkeypatch, 199)
    monkeypatch.setattr(module, "parse_profile", lambda *args, **kwargs: pytest.fail("Ineligible commander profile parsed"))
    monkeypatch.setattr(module, "collect_previews", lambda *args, **kwargs: pytest.fail("Ineligible previews requested"))
    args = parser().parse_args(["--commander", "ghave", "--fetch", "--output-dir", str(tmp_path / "report")])
    with pytest.raises(CommanderBelowMinimum) as failure:
        collect_evidence(args)
    assert failure.value.count == 199 and failure.value.minimum == 200
    assert calls == ["base.json"] and closed == [True]
    assert not args.output_dir.exists()


def test_current_source_count_200_is_accepted_even_without_card_profile_measurements(monkeypatch):
    _, calls, closed = install_base_only_cache(monkeypatch, 200)
    args = parser().parse_args(["--commander", "ghave", "--sample-decks", "0"])
    evidence = collect_evidence(args)
    assert evidence["base"]["deck_count"] == 200
    assert evidence["base"]["cards"] == {}
    assert calls == ["base.json"] and closed == [True]


def test_stricter_source_minimum_is_applied_to_live_page_count(monkeypatch):
    install_base_only_cache(monkeypatch, 200)
    args = parser().parse_args(["--commander", "ghave", "--min-total-decks", "250"])
    with pytest.raises(CommanderBelowMinimum) as failure:
        collect_evidence(args)
    assert failure.value.count == 200 and failure.value.minimum == 250


def test_saved_pending_count_199_cannot_bypass_eligibility_or_write_a_report(monkeypatch, tmp_path):
    module = importlib.import_module("edhrec_affinity.build_styles.__main__")
    monkeypatch.setattr(module, "analyze_decks", lambda *args, **kwargs: pytest.fail("Ineligible saved evidence fitted"))
    args = parser().parse_args(["--commander", "ghave", "--output-dir", str(tmp_path / "report")])
    saved = {"slug": "ghave", "card": {"name": "Ghave"}, "base": {"deck_count": 199}}
    with pytest.raises(CommanderBelowMinimum) as failure:
        analyze_evidence(args, saved)
    assert failure.value.count == 199 and failure.value.minimum == 200
    assert not args.output_dir.exists()
