"""Research invariants: unknowns, staple confounding, overlaps and unlabeled recovery."""

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from edhrec_affinity.build_styles.analysis import (
    analyze_decks, compare_profiles, overlapping_groups, prepare_decks, supported_tag_profiles, tag_label_policy,
)
from edhrec_affinity.build_styles.__main__ import parser, run
from edhrec_affinity.build_styles.report import render_report
from edhrec_affinity.build_styles.sources import (
    Cache, SourceBlocked, extract_preview_data, parse_deck_json, parse_deck_text,
    parse_preview, parse_profile,
)


def profile(slug, rates):
    return {"tag_slug": slug, "tag_name": slug, "deck_count": 1000, "ignored": {},
            "cards": {name: {"name": name, "rate": rate, "count": int(1000 * rate), "denominator": 1000}
                      for name, rate in rates.items()}}


def test_shared_staples_do_not_turn_unrelated_signatures_into_a_group():
    baseline = {f"staple {i}": .9 for i in range(24)}
    baseline.update({f"engine {i}": .05 for i in range(6)})
    left, right = baseline.copy(), baseline.copy()
    left.update({f"engine {i}": .3 for i in range(3)})
    right.update({f"engine {i}": .3 for i in range(3, 6)})
    result = compare_profiles(profile("all", baseline), [profile("a", left), profile("b", right)])
    pair = result["pairs"][0]
    assert pair["raw_similarity"] > .98
    assert pair["similarity"] == 0
    assert result["groups"] == [["a"], ["b"]]


def test_omitted_cards_are_unknown_and_reduce_comparable_evidence():
    baseline = {f"card {i}": .1 for i in range(30)}
    a = profile("a", {key: .5 for key in baseline})
    b = profile("b", {f"card {i}": .5 for i in range(4)})
    result = compare_profiles(profile("all", baseline), [a, b])
    assert result["pairs"][0]["shared_observed_cards"] == 4
    assert result["pairs"][0]["similarity"] is None
    assert len(result["profiles"][1]["signature_cards"]) == 4


def test_profile_summaries_preserve_reported_tag_counts_and_tiers():
    baseline = profile("all", {f"card {i}": .1 for i in range(30)})
    profiles = [{**profile(f"theme-{count}", {key: .5 for key in baseline["cards"]}), "reported_tag_decks": count}
                for count in (4, 5, 14, 15)]
    summaries = compare_profiles(baseline, profiles)["profiles"]
    assert [(row["reported_tag_decks"], row["support_tier"]) for row in summaries] == [
        (5, "supporting"), (14, "supporting"), (15, "main")]
    assert all(row["deck_count"] == 1000 for row in summaries)  # Page denominator is retained separately.
    assert tag_label_policy(10, 20)["main_minimum_decks"] == 20
    assert tag_label_policy(25)["main_minimum_decks"] == 25  # Older single-threshold commands remain valid.
    stricter = supported_tag_profiles([{**profile(str(count), {}), "reported_tag_decks": count}
                                       for count in (9, 10, 19, 20)], min_tag_decks=10, main_tag_decks=20)
    assert [(row["reported_tag_decks"], row["support_tier"]) for row in stricter] == [
        (10, "supporting"), (19, "supporting"), (20, "main")]


def test_tag_can_belong_to_two_groups_without_merging_unrelated_endpoints():
    pairs = [{"left": "counters", "right": "tokens", "similarity": .8},
             {"left": "counters", "right": "proliferate", "similarity": .9},
             {"left": "tokens", "right": "proliferate", "similarity": .1}]
    assert overlapping_groups(["counters", "tokens", "proliferate"], pairs, .6) == [
        ["counters", "proliferate"], ["counters", "tokens"],
    ]


def test_profile_uses_actual_card_denominators_and_excludes_lands_and_new_card_windows():
    cards = [
        {"name": "Engine", "num_decks": 45, "potential_decks": 90, "lift": 3.9},
        {"name": "Recent", "num_decks": 20, "potential_decks": 20, "lift": 4.2},
        {"name": "Land", "num_decks": 70, "potential_decks": 100},
        {"name": "Invalid", "num_decks": 101, "potential_decks": 100},
    ]
    payload = {"container": {"json_dict": {"card": {"num_decks": 100}, "cardlists": [
        {"tag": "topcards", "cardviews": cards},
        {"tag": "lands", "cardviews": [cards[2]]},
    ]}}}
    parsed = parse_profile(payload, "test", "Test")
    assert set(parsed["cards"]) == {"engine"}
    assert parsed["cards"]["engine"]["rate"] == .5
    assert parsed["ignored"]["short_exposure_window"] == 1
    assert parsed["ignored"]["invalid_counts"] == 1
    assert parsed["ranking_cards"]["recent"]["rate"] == 1
    assert parsed["ranking_cards"]["recent"]["lift"] == 4.2
    assert parsed["cards"]["engine"]["lift"] == 3.9


def test_public_preview_reads_cards_without_requiring_tags():
    data = {"deck": {"commander": ["Ghave"], "cards": {
        "Creature": [["Engine", 1]], "Land": [["Forest", 98]], "Sideboard": [["Ignore", 1]],
    }}, "url": "https://archidekt.com/decks/123"}
    html = '<script type="application/json" id="__NEXT_DATA__">' + json.dumps({
        "props": {"pageProps": {"data": data}},
    }) + '</script>'
    deck = parse_preview(extract_preview_data(html), "test-id")
    assert deck["tags"] == []
    assert [card["name"] for card in deck["cards"]] == ["Engine", "Forest"]
    assert deck["source_url"] == "https://archidekt.com/decks/123"


def test_captured_export_adapters_ignore_maybeboards():
    archidekt = {"cards": [
        {"quantity": 1, "categories": ["Commander"], "card": {"oracleCard": {"name": "Ghave"}}},
        {"quantity": 1, "categories": ["Creatures"], "card": {"oracleCard": {"name": "Engine", "types": ["Creature"]}}},
        {"quantity": 1, "categories": ["Maybeboard"], "card": {"oracleCard": {"name": "Ignore"}}},
    ]}
    moxfield = {"boards": {
        "commanders": {"cards": {"a": {"quantity": 1, "card": {"name": "Ghave"}}}},
        "mainboard": {"cards": {"b": {"quantity": 1, "card": {"name": "Engine"}}}},
        "sideboard": {"cards": {"c": {"quantity": 1, "card": {"name": "Ignore"}}}},
    }}
    for data in (archidekt, moxfield):
        result = parse_deck_json(data, "local")
        assert result["commanders"] == ["Ghave"]
        assert [card["name"] for card in result["cards"]] == ["Engine"]


def test_text_export_set_codes_sections_and_commander_validation():
    result = parse_deck_text("Commander\n1 Ghave (CMD) 200\n\nDeck\n1x Engine\n98 Forest\n\nSideboard\n1 Ignore", "test", ["Ghave"])
    assert [(card["name"], card["quantity"]) for card in result["cards"]] == [("Engine", 1), ("Forest", 98)]
    with pytest.raises(ValueError, match="requested commander"):
        parse_deck_text("1 Other Commander\n99 Forest", "test", ["Ghave"])
    with pytest.raises(ValueError, match="Unrecognized export line"):
        parse_deck_text("1 Ghave\nthis is a primer", "test", ["Ghave"])


def test_deck_quality_deduplicates_card_contents_and_filters_wrong_commanders():
    deck = {"id": "one", "commanders": ["Ghave"], "cards": [{"name": "Forest", "quantity": 99}]}
    kept, rejected, duplicates = prepare_decks([
        deck, {**deck, "id": "copy"}, {**deck, "id": "wrong", "commanders": ["Other"]},
        {**deck, "id": "unfinished", "cards": [{"name": "Forest", "quantity": 10}]},
    ], ["Ghave"])
    assert len(kept) == 1
    assert duplicates == 1
    assert len(rejected) == 2


def synthetic_decks():
    decks = []
    for kind in ("a", "b", "hybrid"):
        for i in range(6):
            cards = ([{"name": f"Engine A {j}", "quantity": 1} for j in range(6)] if kind != "b" else [])
            cards += ([{"name": f"Engine B {j}", "quantity": 1} for j in range(6)] if kind != "a" else [])
            cards.append({"name": f"Unique {kind} {i}", "quantity": 1})
            cards.append({"name": "Forest", "quantity": 99 - len(cards), "type_line": "Basic Land"})
            decks.append({"id": f"{kind}-{i}", "commanders": ["Ghave"], "cards": cards, "tags": []})
    return decks


def test_nmf_recovers_overlapping_card_packages_without_deck_tags():
    pytest.importorskip("sklearn")
    base = {"commander_names": ["Ghave"], "land_keys": [], "cards": {}}
    decks = synthetic_decks()
    result = analyze_decks(decks, base, [], components=2)
    assert result["status"] == "fitted"
    assert result["unique_decks"] == 18
    assert result["decks_with_source_tags"] == 0
    assert result["overlap"][0]["deck_count"] == 6
    a_package = next(p["id"] for p in result["packages"] if p["top_cards"][0]["name"].startswith("Engine A"))
    for deck in result["decks"]:
        if deck["id"].startswith("a-"):
            assert deck["memberships"][a_package] > .99
        elif deck["id"].startswith("b-"):
            assert deck["memberships"][a_package] < .01
        else:
            assert deck["memberships"][a_package] == pytest.approx(.5, abs=.01)
    # Contradictory labels must not change the learned mixtures.
    mislabeled = [{**deck, "tags": ["misleading label"]} for deck in decks]
    relabeled = analyze_decks(mislabeled, base, [], components=2)
    np.testing.assert_allclose([d["memberships"] for d in result["decks"]],
                               [d["memberships"] for d in relabeled["decks"]])


def test_access_challenge_stops_collection_without_retry(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(429, text="rate limited")

    cache = Cache(tmp_path, fetch=True)
    cache.client.close()
    cache.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SourceBlocked, match="429"):
            cache.get("one.json", "https://json.edhrec.com/one.json")
        with pytest.raises(SourceBlocked, match="stopped"):
            cache.get("two.json", "https://json.edhrec.com/two.json")
        assert len(calls) == 1
        assert not list(tmp_path.iterdir())
    finally:
        cache.close()


def test_cached_source_can_be_reused_offline(tmp_path):
    (tmp_path / "one.json").write_text('{"hello": "world"}')
    cache = Cache(tmp_path)
    try:
        assert cache.get("one.json", "https://json.edhrec.com/one.json") == {"hello": "world"}
        assert cache.provenance[0]["fetched_at"] is None
        with pytest.raises(FileNotFoundError):
            cache.get("missing.json", "https://json.edhrec.com/missing.json")
    finally:
        cache.close()


def test_report_embedding_does_not_allow_source_text_to_end_script(tmp_path):
    target = tmp_path / "report.html"
    render_report({"name": "</script><script>alert(1)</script>"}, target)
    text = target.read_text()
    assert "\\u003c/script>\\u003cscript>alert(1)" in text
    assert "<script>alert(1)" not in text


@pytest.mark.parametrize("empty_page", [False, True])
@pytest.mark.parametrize("tag_panel", ["empty", "missing_taglinks", "missing_panels"])
def test_offline_cli_writes_an_honest_report_with_insufficient_deck_data(tmp_path, empty_page, tag_panel):
    cache = tmp_path / "cache"
    cache.mkdir()
    payload = {"container": {"json_dict": {
        "card": {"name": "Ghave", "names": ["Ghave"], "num_decks": 200, "url": "/commanders/ghave"},
        "cardlists": [{"tag": "creatures", "cardviews": [
            {"name": "Engine", "num_decks": 80, "potential_decks": 200},
        ]}],
    }}, "panels": {"taglinks": []}}
    if tag_panel == "missing_taglinks":
        payload["panels"].pop("taglinks")
    elif tag_panel == "missing_panels":
        payload.pop("panels")
    if empty_page:
        payload["container"]["json_dict"]["cardlists"] = []
    (cache / "base.json").write_text(json.dumps(payload))
    output = tmp_path / "report"
    args = parser().parse_args(["--commander", "ghave", "--sample-decks", "0",
                                "--cache-dir", str(cache), "--output-dir", str(output)])
    report = run(args)
    assert report["profiles"]["profiles"] == []
    assert report["deck_analysis"]["status"] == "insufficient_data"
    assert report["collection"]["network_enabled"] is False
    assert json.loads((output / "report.json").read_text())["commander_name"] == "Ghave"
    assert (output / "index.html").is_file()


def test_package_cli_defaults_to_auto_and_accepts_one():
    assert parser().parse_args(["--commander", "ghave"]).components is None
    assert parser().parse_args(["--commander", "ghave", "--components", "auto"]).components is None
    assert parser().parse_args(["--commander", "ghave", "--components", "1"]).components == 1
    assert parser().parse_args(["--commander", "ghave", "--max-components", "12"]).max_components == 12
    with pytest.raises(SystemExit):
        parser().parse_args(["--commander", "ghave", "--components", "0"])
