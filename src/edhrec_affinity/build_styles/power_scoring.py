"""Calculate a local power estimate from the public EDHPowerLevel formula.

The arithmetic and adjustments were inspected in the site's public client:
https://edhpowerlevel.com/js/main-C1lbqCDd.js. No JavaScript is executed and no
request is made here. Prices, ranks and Oracle metadata come from Scryfall.
The selected Scryfall printing can differ from the site's lowest-price printing
and its cached data; this is explicitly a local estimate, never an externally
verified EDHPowerLevel result. Unknown or stale inputs cannot establish a score.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import re
from typing import Any, Iterable

from .external_validation import cardlist_hash
from .power_brackets import bracket_for_power_score
from .sources import card_key


POWER_FORMULA_VERSION = 1
ALGORITHM_SOURCE_URL = "https://edhpowerlevel.com/js/main-C1lbqCDd.js"
ALGORITHM_SHA256 = "f0c8bb9977b9d749280a0484454123ae05da39b18d29e8702d1c02d12c122d49"
# The same public client's easily chained extra-turn list, separate from its
# overall count of ordinary extra-turn cards. Brackets four and five are open.
CHAINABLE_EXTRA_TURN_CARDS = frozenset(card_key(name) for name in (
    "Time Warp", "Temporal Manipulation", "Walk the Aeons", "Capture of Jingzhou",
    "Expropriate", "Time Stretch", "Nexus of Fate", "Timestream Navigator",
    "Sage of Hours", "Lighthouse Chronologist", "Time Sieve", "Magosi, the Waterveil",
))
EXTRA_TURN_LIMITS = (0, 2, 3, None, None)
_PRICE_CURVE = (0, .5, 1.5, 3.5, 6, 10, 15, 25, 40, 65, 100)
_POPULARITY_CURVE = (0, 8500, 13600, 17100, 19800, 21900, 23700, 25300, 26200, 26700, 27000)
_POWER_CURVE = (0, 250, 320, 350, 380, 420, 470, 560, 760, 890, 1000)
_USD_FIELDS = ("usd", "usd_foil", "usd_etched")
_DECIMAL = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_BASICS = frozenset(card_key(name) for name in (
    "Mountain", "Forest", "Island", "Swamp", "Plains", "Wastes",
    "Snow-Covered Mountain", "Snow-Covered Forest", "Snow-Covered Island",
    "Snow-Covered Swamp", "Snow-Covered Plains", "Snow-Covered Wastes",
))
_PRICE_ADJUSTMENTS = {card_key(name): value for name, value in {
    "Mystic Remora": 2, "Rest in Peace": 2.5, "The One Ring": .8,
    "Sylvan Library": .6, "Ragavan, Nimble Pilferer": .4, "Sol Ring": 8,
}.items()}
_IMPACT_ADJUSTMENTS = {card_key(name): value for name, value in {
    "Cataclysm": 1.7, "Jokulhaups": 1.7, "Boom // Bust": 1.7, "Armageddon": 1.4,
}.items()}
_COMMANDER_ADJUSTMENTS = {card_key(name): value for name, value in {
    "Vivi Ornitier": 3, "Korvold, Fae-Cursed King": 3, "Chulane, Teller of Tales": 3,
    "Yuriko, the Tiger's Shadow": 3, "Shirei, Shizo's Caretaker": 2.5,
    "Orvar, the All-Form": 3, "Magda, Brazen Outlaw": 3.5,
    "Tergrid, God of Fright // Tergrid's Lantern": 2.5, "Winota, Joiner of Forces": 3,
    "Sisay, Weatherlight Captain": 4, "Urza, Lord High Artificer": 3,
    "Kinnan, Bonder Prodigy": 4, "Yedora, Grave Gardener": 2,
    "Kraum, Ludevic's Opus": 2, "Thrasios, Triton Hero": 3.5,
    "Tymna the Weaver": 2, "Vial Smasher the Fierce": 2,
}.items()}
_CMC_ADJUSTMENTS = {card_key(name): 0 for name in (
    "Fierce Guardianship", "Deflecting Swat", "Deadly Rollick", "Flawless Maneuver",
    "Obscuring Haze", "Flare of Denial", "Flare of Fortitude", "Flare of Duplication",
    "Flare of Malice", "Flare of Cultivation", "Endurance", "Solitude", "Grief",
    "Subtlety", "Fury", "Force of Vigor", "Force of Negation", "Force of Despair",
    "Force of Virtue", "Force of Rage", "Force of Will", "Misdirection", "Submerge",
    "Snuff Out", "Daze", "Foil", "Gush",
)}
_CMC_ADJUSTMENTS.update({card_key(name): cmc for name, cmc in {
    "Shriekmaw": 2, "Blasphemous Act": 3, "The Great Henge": 5, "Vandalblast": 3,
    "Cyclonic Rift": 5, "Everflowing Chalice": 2, "Dig Through Time": 4,
    "Temporal Trespass": 7, "Treasure Cruise": 3, "Emrakul, the Promised End": 7,
}.items()})


def _curve(value: float, stops: tuple, weight: float = 1) -> float:
    """Match the public client's weighted-stop arithmetic, including its edges.

    The client weights the lower integer stop, then adds the unweighted fraction.
    At exactly the last stop its loop returns zero; retain that published
    behavior so this version does not quietly substitute a different formula.
    """
    if value <= stops[0]:
        return 0
    if value > stops[-1]:
        return (len(stops) - 1) * weight
    for index, (low, high) in enumerate(zip(stops, stops[1:])):
        if low <= value < high:
            return index * weight + (value - low) / (high - low)
    return 0


def _round2(value: float) -> float:
    # JavaScript Number.toFixed rounds the exact underlying binary number.
    return float(Decimal(value).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _usd(prices: Any) -> float | None:
    if not isinstance(prices, dict):
        raise ValueError("Missing Scryfall USD prices")
    values = []
    for field in _USD_FIELDS:
        value = prices.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
            raise ValueError("Invalid Scryfall USD price")
        try:
            parsed = float(Decimal(value))
        except (InvalidOperation, ValueError, OverflowError) as error:
            raise ValueError("Invalid Scryfall USD price") from error
        if not math.isfinite(parsed):
            raise ValueError("Invalid Scryfall USD price")
        values.append(parsed)
    return min(values) if values else None


def _checked(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Missing metadata timestamp")
    try:
        checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid metadata timestamp") from error
    if checked.tzinfo is None:
        raise ValueError("Metadata timestamp must include a timezone")
    return checked.astimezone(timezone.utc)


def calculate_power_rating(
    cards: Iterable[dict], metadata: dict[str, dict], *,
    commanders: Iterable[str] | None = None, now: datetime | None = None,
    max_age_days: float = 7,
) -> dict:
    """Return a deterministic, exact-list local estimate or explicit omissions.

    ``metadata`` is keyed by normalized full name. ``commanders`` contains full
    card names; only these cards receive a commander-specific impact adjustment.
    A complete fresh 100-card list is required, and no missing price/rank is
    silently replaced with zero. An explicitly observed null rank denotes an
    unranked card, whose popularity contribution is zero in the public client.
    ``checked_at`` dates the oldest input, so it does not imply a site visit.
    """
    result = {
        "status": "unavailable", "source": "local_public_formula",
        "method": "edhpowerlevel_public_client_scryfall",
        "power_formula_version": POWER_FORMULA_VERSION,
        "algorithm_sha256": ALGORITHM_SHA256, "source_url": ALGORITHM_SOURCE_URL,
        "is_estimate": True, "missing_cards": [], "stale_cards": [], "invalid_cards": [],
    }
    try:
        if isinstance(cards, (str, bytes, dict)):
            raise ValueError("Expected a list of cards")
        rows = list(cards)
        result["deck_sha256"] = cardlist_hash(rows)
        quantities = Counter()
        names = {}
        for row in rows:
            key = card_key(row["name"])
            quantities[key] += row.get("quantity", 1)
            names[key] = row["name"]
        count = sum(quantities.values())
        result["card_count"] = count
        if count != 100:
            raise ValueError("Power calculation requires the complete 100-card list")
        if not isinstance(metadata, dict):
            raise ValueError("Expected normalized Scryfall metadata")
        if isinstance(commanders, (str, bytes, dict)):
            raise ValueError("Commander names must be a list")
        commander_keys = set()
        for name in commanders or ():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Commander names must be nonempty strings")
            key = card_key(name)
            if key not in quantities:
                raise ValueError("Commander is absent from the scored list")
            commander_keys.add(key)
        if isinstance(max_age_days, bool) or _finite(max_age_days) is None or max_age_days <= 0:
            raise ValueError("Metadata maximum age must be positive")
        current = now or datetime.now(timezone.utc)
        if not isinstance(current, datetime) or current.tzinfo is None:
            raise ValueError("Calculation time must include a timezone")
        inputs, dates = [], []
        for key in sorted(quantities):
            card = metadata.get(key)
            if card is None:
                result["missing_cards"].append(names[key])
                continue
            try:
                if not isinstance(card, dict):
                    raise ValueError("Invalid Scryfall card")
                checked = _checked(card.get("checked_at"))
                age = current - checked
                if age >= timedelta(days=max_age_days) or age < timedelta(minutes=-5):
                    result["stale_cards"].append(names[key])
                    continue
                price_proof = card.get("power_price_fallback")
                price_checked, price_printing = checked, None
                if price_proof is not None:
                    if not isinstance(price_proof, dict) or price_proof.get("status") != "resolved":
                        raise ValueError("No available priced physical printing for this card")
                    price_checked = _checked(price_proof.get("checked_at"))
                    price_age = current - price_checked
                    if price_age >= timedelta(days=max_age_days) or price_age < timedelta(minutes=-5):
                        result["stale_cards"].append(names[key])
                        continue
                    if (price_proof.get("oracle_id") != card.get("oracle_id")
                            or price_proof.get("prices") != card.get("prices")
                            or not isinstance(price_proof.get("printing_id"), str)):
                        raise ValueError("Priced-printing evidence does not identify this card's prices")
                    price_printing = price_proof["printing_id"]
                price = _usd(card.get("prices"))
                if price is None and key not in _BASICS:
                    raise ValueError("No available Scryfall USD price")
                rank = card["edhrec_rank"]
                if rank is not None and (type(rank) is not int or rank < 1):
                    raise ValueError("Invalid Scryfall EDHREC rank")
                reserved = card["reserved"]
                if not isinstance(reserved, bool):
                    raise ValueError("Missing Scryfall reserved flag")
                cmc = _finite(card.get("cmc"))
                if cmc is None:
                    raise ValueError("Invalid Scryfall mana value")
                layout, type_line = card.get("layout"), card.get("type_line")
                if not isinstance(layout, str) or not layout or not isinstance(type_line, str) or not type_line:
                    raise ValueError("Missing Scryfall layout or type")
                # The client puts impact into a discrete CMC array. Unknown
                # fractional entries cannot be reproduced as a reliable score.
                adjusted_cmc = _CMC_ADJUSTMENTS.get(key, cmc)
                if adjusted_cmc != int(adjusted_cmc):
                    raise ValueError("Fractional mana value is unsupported by the public formula")
                dates.append(checked)
                dates.append(price_checked)
                inputs.append({
                    "key": key, "quantity": quantities[key], "price": price,
                    "edhrec_rank": rank, "reserved": reserved, "cmc": cmc,
                    "layout": layout, "type_line": type_line,
                    "commander": key in commander_keys,
                    "checked_at": checked.isoformat(),
                    "price_checked_at": price_checked.isoformat(), "price_printing_id": price_printing,
                })
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                result["invalid_cards"].append({"name": names[key], "reason": str(error)})
        if result["missing_cards"] or result["stale_cards"] or result["invalid_cards"]:
            result["reason"] = "Complete fresh price, rank and Oracle metadata is required for every card."
            return result
        encoded = json.dumps(inputs, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        result["input_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
        total_impact, cmc_sum, nonland_count = 0.0, 0.0, 0
        nonland_impact = Counter()
        for card in inputs:
            key, quantity = card["key"], card["quantity"]
            price = (card["price"] or 0) * _PRICE_ADJUSTMENTS.get(key, 1)
            if card["reserved"]:
                price *= .2
            popularity = 0 if card["edhrec_rank"] is None else _curve(27000 - card["edhrec_rank"], _POPULARITY_CURVE, .75)
            impact = (_curve(price, _PRICE_CURVE, 1.25) + popularity) * quantity
            impact *= _IMPACT_ADJUSTMENTS.get(key, 1)
            if card["commander"]:
                impact *= _COMMANDER_ADJUSTMENTS.get(key, 1)
            is_land = "Land" in card["type_line"].split(" // ")[0].split(" — ")[0] or card["layout"] == "modal_dfc"
            cmc = _CMC_ADJUSTMENTS.get(key, card["cmc"])
            if is_land:
                impact *= .6
                cmc = 0
            if key in _BASICS:
                impact = 2 * quantity
            total_impact += impact
            if card["layout"] != "modal_dfc":
                cmc_sum += cmc * quantity
            if not is_land:
                nonland_count += quantity
                nonland_impact[int(cmc)] += impact
        if nonland_count == 0:
            raise ValueError("The public formula requires at least one nonland card")
        average_cmc = _round2(cmc_sum / nonland_count)
        cumulative, tipping_point = 0.0, 0
        threshold = sum(nonland_impact.values()) * .65
        for cmc, impact in sorted(nonland_impact.items()):
            cumulative += impact
            if cumulative > threshold:
                tipping_point = cmc
                break
        efficiency = (6 - (average_cmc + tipping_point) / 2) / (6 - 1.75)
        adjusted_impact = total_impact * (.65 + .45 * efficiency)
        if not all(math.isfinite(value) for value in (total_impact, average_cmc, efficiency, adjusted_impact)):
            raise ValueError("Power calculation exceeded finite numerical limits")
        power_level = _round2(_curve(adjusted_impact, _POWER_CURVE))
        result.update({
            "status": "calculated", "power_level": power_level,
            "score_bracket": bracket_for_power_score(power_level),
            "checked_at": min(dates).isoformat(),
            "metadata_checked_at": min(dates).isoformat(),
            "total_impact": round(total_impact, 6), "adjusted_impact": round(adjusted_impact, 6),
            "average_cmc": average_cmc, "tipping_point": tipping_point,
            "efficiency": round(efficiency * 10, 6),
            "price_basis": "minimum_available_usd_finish_for_returned_scryfall_printing",
            "note": "Local estimate using public arithmetic and Scryfall data; the live site's prices and ranks may differ.",
        })
    except (ValueError, TypeError, KeyError, OverflowError, InvalidOperation) as error:
        result["reason"] = str(error)
    return result


calculate_deck_power = calculate_power_rating
