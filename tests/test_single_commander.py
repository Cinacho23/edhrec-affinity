"""
test_single_commander.py

This file tests the single-commander parser.

The test does not call EDHREC directly. Instead, it uses a small fake payload
taht has the same structure as the real JSON fields we care about.

This is intentional:
- live website data cna change
- network requests can fail
- unit tests should focus on our parsing logic
"""
from edhrec_affinity.single_commander import parse_commander_payload

def test_parse_commander_payload_extracts_tags():
    """
    Test that parse_commander_payload() correctly extracts:
    - total deck count
    - tag names
    - tag slugs
    - tag deck counts
    - source type

    This fake payload mirrors the real structure:
    payload["num_decks_avg"]
    payload["panels"]["taglinks"]
    """

    # Fake EDHREC-style JSON payload
    #
    # We only include the fields needed by the parser.
    # The real EDHREC JSON has many more fields, but the parser does not need
    # all of them.
    fake_payload = {
        "num_decks_avg": 1000,
        "panels": {
            "taglinks": [
                {"count": 250, "slug": "tokens", "value": "Tokens"},
                {"count": 100, "slug": "aggro", "value": "Aggro"},
            ],
        }
    }

    # Parse the fake payload as if it came from a real commander JSON file.
    result = parse_commander_payload(
        payload=fake_payload,
        commander_slug="example-commander",
        commander_name="Example Commander",
    )

    # Confirm the top-level commander deck count was extracted.
    assert result.total_decks == 1000

    # Confirm both fake tags were parsed.
    assert len(result.tags) == 2

    # Confirm the first tag row was normalized correctly.
    assert result.tags[0].tag_name == "Tokens"
    assert result.tags[0].tag_slug == "tokens"
    assert result.tags[0].tag_decks == 250
    assert result.tags[0].source_type == "commander_json"

    # Confirm commander metadata was copied into the tag row.
    assert result.tags[0].commander_name == "Example Commander"
    assert result.tags[0].commander_slug == "example-commander"
    assert result.tags[0].total_decks == 1000