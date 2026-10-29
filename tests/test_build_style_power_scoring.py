"""Local arithmetic has dated inputs and never masquerades as a site check."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from edhrec_affinity.build_styles.external_validation import cardlist_hash
from edhrec_affinity.build_styles.power_scoring import calculate_power_rating
from edhrec_affinity.build_styles.power_scoring import CHAINABLE_EXTRA_TURN_CARDS, EXTRA_TURN_LIMITS
from edhrec_affinity.build_styles.sources import card_key


NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
CHECKED = (NOW - timedelta(hours=1)).isoformat()


def meta(name, *, price="1.00", rank=300, cmc=2, land=False, **changes):
    return {"name": name, "cmc": cmc, "layout": "normal",
            "type_line": "Basic Land" if land else "Creature",
            "prices": {"usd": price, "usd_foil": None, "usd_etched": None},
            "edhrec_rank": rank, "reserved": False, "checked_at": CHECKED, **changes}


def sample(*, spell="Test Spell", commander="Test Commander"):
    cards = [{"name": commander, "quantity": 1}, {"name": spell, "quantity": 63},
             {"name": "Island", "quantity": 36}]
    metadata = {card_key(commander): meta(commander), card_key(spell): meta(spell),
                "island": meta("Island", price=None, rank=None, cmc=0, land=True)}
    return cards, metadata


def calculate(cards, metadata, **kwargs):
    return calculate_power_rating(cards, metadata, now=NOW, **kwargs)


def test_calculation_is_dated_local_estimate_for_exact_card_list():
    cards, metadata = sample()
    rating = calculate(cards, metadata, commanders=["Test Commander"])
    assert rating["status"] == "calculated"
    assert rating["source"] == "local_public_formula"
    assert rating["is_estimate"] is True
    assert rating["deck_sha256"] == cardlist_hash(cards)
    assert rating["card_count"] == 100
    assert rating["checked_at"] == CHECKED
    # Each spell has 1.75 price + 6.75 popularity impact; lands are 2 each.
    assert rating["total_impact"] == 616
    assert rating["average_cmc"] == 2
    assert rating["tipping_point"] == 2
    assert rating["efficiency"] == pytest.approx(9.411765)
    assert rating["power_level"] == 7.51
    assert rating["score_bracket"] == 3
    assert "playability_pct" not in rating
    assert rating == calculate(cards, metadata, commanders=["Test Commander"])


def test_price_or_rank_change_recalculates_same_list_without_changing_its_hash():
    cards, metadata = sample()
    initial = calculate(cards, metadata)
    changed = deepcopy(metadata)
    changed["test spell"]["prices"]["usd"] = ".01"
    later = calculate(cards, changed)
    assert initial["deck_sha256"] == later["deck_sha256"]
    assert initial["input_sha256"] != later["input_sha256"]
    assert initial["power_level"] > later["power_level"]


def test_minimum_available_usd_finish_and_reserved_adjustment_are_explicit():
    cards, metadata = sample()
    metadata["test spell"]["prices"] = {"usd": None, "usd_foil": "5.00", "usd_etched": "1.00"}
    assert calculate(cards, metadata)["total_impact"] == 616
    metadata["test spell"]["reserved"] = True
    discounted = calculate(cards, metadata)
    assert discounted["total_impact"] < 616
    assert discounted["price_basis"] == "minimum_available_usd_finish_for_returned_scryfall_printing"


def test_commander_impact_adjustment_requires_explicit_commander_role():
    cards, metadata = sample(commander="Kinnan, Bonder Prodigy")
    no_role = calculate(cards, metadata)
    commander = calculate(cards, metadata, commanders=["Kinnan, Bonder Prodigy"])
    assert commander["total_impact"] - no_role["total_impact"] == pytest.approx(25.5)
    assert commander["input_sha256"] != no_role["input_sha256"]


def test_free_spell_adjustment_changes_effective_cost():
    cards, metadata = sample(spell="Force of Will")
    metadata["force of will"]["cmc"] = 5
    rating = calculate(cards, metadata)
    assert rating["average_cmc"] == .03
    assert rating["tipping_point"] == 0


def test_modal_dfc_counts_as_land_without_its_spell_mana_cost():
    cards, metadata = sample(spell="Test MDFC")
    metadata["test mdfc"]["layout"] = "modal_dfc"
    metadata["test mdfc"]["cmc"] = 7
    rating = calculate(cards, metadata)
    assert rating["average_cmc"] == 2
    assert rating["tipping_point"] == 2
    assert rating["total_impact"] == pytest.approx(401.8)


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "1"])
def test_invalid_card_quantities_never_get_a_score(quantity):
    cards, metadata = sample()
    cards[0]["quantity"] = quantity
    rating = calculate(cards, metadata)
    assert rating["status"] == "unavailable"
    assert "power_level" not in rating


@pytest.mark.parametrize("field", ["prices", "edhrec_rank", "reserved", "cmc", "layout", "type_line", "checked_at"])
def test_missing_inputs_do_not_become_zero_or_verified(field):
    cards, metadata = sample()
    del metadata["test spell"][field]
    rating = calculate(cards, metadata)
    assert rating["status"] == "unavailable"
    assert "power_level" not in rating
    assert rating["invalid_cards"][0]["name"] == "Test Spell"
    assert "checked_at" not in rating


def test_explicit_unranked_card_differs_from_missing_rank():
    cards, metadata = sample()
    metadata["test spell"]["edhrec_rank"] = None
    rating = calculate(cards, metadata)
    assert rating["status"] == "calculated"
    assert rating["total_impact"] == pytest.approx(190.75)


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-1", "1e4", "", 1, True, "9" * 1000])
def test_invalid_usd_prices_fail_closed(price):
    cards, metadata = sample()
    metadata["test spell"]["prices"]["usd"] = price
    rating = calculate(cards, metadata)
    assert rating["status"] == "unavailable"
    assert rating["invalid_cards"]


@pytest.mark.parametrize("rank", [False, True, -1, 0, "10", 1.5, float("nan")])
def test_invalid_rank_is_not_a_zero_popularity_substitute(rank):
    cards, metadata = sample()
    metadata["test spell"]["edhrec_rank"] = rank
    assert calculate(cards, metadata)["status"] == "unavailable"


@pytest.mark.parametrize("offset", [timedelta(days=-7), timedelta(minutes=6)])
def test_stale_or_future_metadata_does_not_establish_power(offset):
    cards, metadata = sample()
    metadata["test spell"]["checked_at"] = (NOW + offset).isoformat()
    rating = calculate(cards, metadata)
    assert rating["status"] == "unavailable"
    assert rating["stale_cards"] == ["Test Spell"]
    assert "power_level" not in rating


def test_missing_metadata_card_and_incomplete_deck_are_explicit():
    cards, metadata = sample()
    metadata.pop("test spell")
    rating = calculate(cards, metadata)
    assert rating["status"] == "unavailable"
    assert rating["missing_cards"] == ["Test Spell"]
    cards[-1]["quantity"] -= 1
    incomplete = calculate(cards, metadata)
    assert incomplete["card_count"] == 99
    assert "complete 100-card" in incomplete["reason"]


def test_calculation_is_order_independent_and_does_not_mutate_inputs():
    cards, metadata = sample()
    before = deepcopy((cards, metadata))
    rating = calculate(cards, metadata)
    assert rating == calculate(list(reversed(cards)), metadata)
    assert (cards, metadata) == before


def test_ordinary_turns_and_easily_chained_turns_have_distinct_limits():
    assert "walk the aeons" in CHAINABLE_EXTRA_TURN_CARDS
    assert "part the waterveil" not in CHAINABLE_EXTRA_TURN_CARDS
    assert EXTRA_TURN_LIMITS == (0, 2, 3, None, None)
    assert len(CHAINABLE_EXTRA_TURN_CARDS) == 12


def test_no_nonland_cards_cannot_establish_an_efficiency_score():
    cards = [{"name": "Island", "quantity": 100}]
    metadata = {"island": meta("Island", price=None, rank=None, cmc=0, land=True)}
    rating = calculate(cards, metadata)
    assert rating["status"] == "unavailable"
    assert "at least one nonland" in rating["reason"]


def test_fractional_cost_and_impossible_commander_are_explicitly_unavailable():
    cards, metadata = sample()
    metadata["test spell"]["cmc"] = .5
    assert calculate(cards, metadata)["status"] == "unavailable"
    rating = calculate(cards, metadata, commanders=["Another Commander"])
    assert rating["status"] == "unavailable"
    assert "absent" in rating["reason"]


def test_same_oracle_price_evidence_is_dated_separately_from_fresh_rules_metadata():
    cards, metadata = sample()
    spell = metadata["test spell"]
    spell["oracle_id"] = "same-oracle"
    price_checked = (NOW - timedelta(days=2)).isoformat()
    spell["power_price_fallback"] = {
        "status": "resolved", "oracle_id": "same-oracle", "printing_id": "another-printing",
        "checked_at": price_checked, "prices": deepcopy(spell["prices"]),
    }
    rating = calculate(cards, metadata)
    assert rating["status"] == "calculated"
    assert rating["checked_at"] == price_checked
    assert rating["power_level"] == 7.51
    spell["power_price_fallback"]["checked_at"] = (NOW - timedelta(days=8)).isoformat()
    stale = calculate(cards, metadata)
    assert stale["status"] == "unavailable"
    assert stale["stale_cards"] == ["Test Spell"]


@pytest.mark.parametrize("changes", [
    {"status": "unavailable"}, {"oracle_id": "different-card"},
    {"prices": {"usd": ".01"}}, {"printing_id": None},
])
def test_incomplete_or_mismatched_price_evidence_cannot_establish_power(changes):
    cards, metadata = sample()
    spell = metadata["test spell"]
    spell["oracle_id"] = "same-oracle"
    spell["power_price_fallback"] = {
        "status": "resolved", "oracle_id": "same-oracle", "printing_id": "another-printing",
        "checked_at": CHECKED, "prices": deepcopy(spell["prices"]), **changes,
    }
    rating = calculate(cards, metadata)
    assert rating["status"] == "unavailable"
    assert rating["invalid_cards"] and "power_level" not in rating


# Minimal numerical facts from the public client's embedded Child of Alara
# reference list. No online lookup or execution of downloaded JavaScript occurs.
# name; quantity; USD; mana value; EDHREC rank; land / modal / transform flag
_REFERENCE = """Child of Alara;1;0.60;5;7924;
Akroma's Vengeance;1;0.36;6;4680;
Ardent Elementalist;1;0.15;4;6072;
Austere Command;1;0.33;6;148;
Bala Ged Recovery // Bala Ged Sanctuary;1;4.51;3;272;M
Beanstalk Giant // Fertile Footsteps;1;0.09;7;1634;
Body Double;1;0.10;5;4771;
Boompile;1;0.31;4;5971;
Boundless Realms;1;1.27;7;5114;
Cackling Counterpart;1;0.13;3;2634;
Canopy Vista;1;0.19;0;99;L
Circuitous Route;1;0.35;4;1516;
Cleansing Nova;1;0.48;5;915;
Cloudblazer;1;0.05;5;2618;
Command Tower;1;0.17;0;2;L
Consuming Tide;1;0.48;4;3508;
Cultivate;1;0.59;3;15;
Darksteel Ingot;1;0.17;3;1476;
Decree of Pain;1;0.23;8;1095;
Devastation Tide;1;0.23;5;2918;
Disdainful Stroke;1;0.01;2;2092;
Dispel;1;0.20;1;631;
Displace;1;0.24;3;2146;
Dread Return;1;0.22;4;503;
Dualcaster Mage;1;0.87;3;613;
Erebos, Bleak-Hearted;1;0.81;4;2985;
Eternal Witness;1;1.09;3;66;
Evolving Wilds;1;0.04;0;18;L
Exotic Orchard;1;0.16;0;9;L
Explore;1;0.14;2;243;
Farseek;1;0.92;2;27;
Flood of Tears;1;0.80;6;3097;
Forest;12;0.05;0;;L
Generous Gift;1;0.33;3;41;
Ghostly Flicker;1;0.75;3;588;
Grim Backwoods;1;0.10;0;1998;L
High Market;1;3.21;0;425;L
Highland Forest;1;0.35;0;1924;L
Hour of Promise;1;0.10;5;2495;
Ichor Wellspring;1;0.21;2;908;
Illusionist's Stratagem;1;0.26;4;5884;
Island;4;0.04;0;;L
Journey to Eternity // Atzal, Cave of Eternity;1;4.87;3;2536;T
Karmic Guide;1;0.41;5;702;
Kederekt Leviathan;1;0.34;8;7916;
Kodama's Reach;1;1.21;3;33;
Krosan Verge;1;0.08;0;534;L
Late to Dinner;1;0.04;4;6632;
Life's Legacy;1;2.43;2;1778;
Living Death;1;2.79;5;493;
Morbid Curiosity;1;0.13;3;7501;
Mountain;2;0.05;0;;L
Myriad Landscape;1;0.15;0;21;L
Naru Meha, Master Wizard;1;0.48;4;5091;
Negate;1;0.10;2;47;
Neoform;1;0.81;2;1444;
Nevinyrral's Disk;1;0.28;4;1334;
Path of Ancestry;1;0.13;0;14;L
Peregrine Drake;1;0.14;5;873;
Plains;2;0.04;0;;L
Prairie Stream;1;0.16;0;103;L
Primal Growth;1;0.32;3;4589;
Rampant Growth;1;0.33;2;26;
Recurring Insight;1;1.82;6;8973;
Saheeli's Artistry;1;0.13;6;4840;
Search for Tomorrow;1;0.17;3;997;
Seasons Past;1;0.77;6;5910;
Skullwinder;1;0.07;3;2728;
Skyshroud Claim;1;1.42;4;254;
Smoldering Marsh;1;0.19;0;104;L
Sun Titan;1;0.26;6;230;
Sunken Hollow;1;0.21;0;83;L
Swamp;2;0.06;0;;L
Terramorphic Expanse;1;0.07;0;28;L
Timeless Witness;1;0.24;4;1210;
Twinflame;1;2.41;2;1063;
Unburial Rites;1;0.13;5;3049;
Vastwood Surge;1;0.14;4;4049;
Victimize;1;0.33;3;143;
Wall of Blossoms;1;0.20;2;2525;
Wall of Omens;1;0.21;2;1154;
Wayfarer's Bauble;1;0.22;1;62;
Wood Elves;1;0.21;3;400;"""


def test_public_reference_deck_reproduces_555_without_network_or_javascript():
    cards, metadata = [], {}
    for line in _REFERENCE.splitlines():
        name, quantity, price, cmc, rank, flag = line.split(";")
        cards.append({"name": name, "quantity": int(quantity)})
        metadata[card_key(name)] = meta(
            name, price=price, rank=int(rank) if rank else None, cmc=int(cmc),
            land=flag == "L", layout={"M": "modal_dfc", "T": "transform"}.get(flag, "normal"),
        )
    rating = calculate(cards, metadata, commanders=["Child of Alara"])
    assert rating["status"] == "calculated"
    assert rating["power_level"] == 5.55
    assert rating["score_bracket"] == 2
    assert rating["total_impact"] == pytest.approx(516.002275)
    assert rating["adjusted_impact"] == pytest.approx(447.404326)
    assert rating["average_cmc"] == 3.90
    assert rating["tipping_point"] == 4
