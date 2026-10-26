"""Count-based package labels keep source collection and evidence separate."""

import importlib

import pytest

from edhrec_affinity.build_styles.__main__ import collect_evidence, parser
from edhrec_affinity.build_styles.analysis import tag_label_policy


def test_collection_fetches_supporting_and_main_profiles_but_skips_under_five(monkeypatch):
    module = importlib.import_module("edhrec_affinity.build_styles.__main__")
    counts = {f"theme-{count}": count for count in (4, 5, 14, 15)}
    calls = []

    class Cache:
        blocked = False
        provenance = []

        def __init__(self, *args, **kwargs):
            pass

        def get(self, relative, url, **kwargs):
            calls.append(relative)
            count = 200 if relative == "base.json" else counts[relative.removesuffix(".json")]
            return {"container": {"json_dict": {"card": {
                "name": "Ghave", "names": ["Ghave"], "num_decks": count,
                "url": "/commanders/ghave" if relative == "base.json" else url,
            }, "cardlists": [{"tag": "topcards", "cardviews": [
                {"name": "Engine", "num_decks": count, "potential_decks": count},
            ]}]}}, "panels": {"taglinks": [{"slug": slug, "value": slug, "count": count}
                                                       for slug, count in counts.items()]}}

        def close(self):
            pass

    class Aliases:
        def groups(self, card):
            return [["Ghave"]]

    monkeypatch.setattr(module, "Cache", Cache)
    args = parser().parse_args(["--commander", "ghave", "--sample-decks", "0"])
    result = collect_evidence(args, commander_alias_provider=Aliases())
    assert calls == ["base.json", "theme-15.json", "theme-14.json", "theme-5.json"]
    assert [(profile["reported_tag_decks"], profile["support_tier"]) for profile in result["profiles"]] == [
        (15, "main"), (14, "supporting"), (5, "supporting")]
    assert result["tag_label_policy"] == tag_label_policy()


def test_collection_rejects_explicit_reversed_thresholds_before_source_access():
    args = parser().parse_args(["--commander", "ghave", "--min-tag-decks", "16", "--main-tag-decks", "15"])
    with pytest.raises(ValueError, match="Tag thresholds"):
        collect_evidence(args)
