"""
models.py

This file defines the data structures for the scraper.

The goal is not to scrape yet.
The goal is to define what valid scraped data should look like.

Later, the scraper will fetch raw EDHREC JSON and convert it into these models.
"""

# datetime is used to record when the scrape happened.
# timezone lets us create a timezone-aware UTC timestamp.
from datetime import datetime, timezone

# BaseModel is the base class for Pydantic models.
# Field lets us add validation rules, such as "this number must be >= 0".
from pydantic import BaseModel, Field

class CommanderTagRow(BaseModel):
    """
    Represents one commander-tag relationship.

    Example:
    Jasmine Boreal of the Seven + Vanilla
    Jasmine Boreal of the Seven + Power
    Jasmine Boreal of the Seven + Aggro

    Each tag becomes its own row because later analysis will compare
    commanders within each tag.
    """

    # Human-readable commander name.
    # Example: "Jasmine Boreal of the Seven"
    commander_name: str

    # EDHREC URL-friendly commander identifier.
    # Example: "jasmine-boreal-of-the-seven"
    commander_slug: str

    # Total number of decks for this commander.
    # ge=0 means "greater than or equal to zero".
    # Negative deck counts should never be valid.
    total_decks: int = Field(ge=0)

    # Human-readable tag name.
    # Example: "Vanilla"
    tag_name: str

    # URL-friendly / stable tag identifier.
    # Example: "vanilla"
    tag_slug: str

    # Number of this commander's decks that belong to his tag.
    # Example: 210 Vanilla decks out of 5722 total decks.
    # This also cannot be negative.
    tag_decks: int = Field(ge=0)

    # Records where this row came from.
    # For normal tags, this should be "commander_json".
    # Later, cEDH rows may use something like "cedh_filtered_json".
    source_type: str = "commander_json"

    # Timestamp showing when this scrape happened.
    # This is important later for weekly/monthly trend tracking.
    scrape_timestamp: str

class CommanderScrapeResult(BaseModel):
    """
    Represents the complete scrape result for one commander.

    This is the parent object.

    It contains:
    - the commander identity
    - total deck count
    - a list of CommanderTagRow objects
    - the scrape timestamp
    """

    # Human-readable commander name.
    commander_name: str

    # EDHREC commander slug.
    commander_slug: str

    # Total deck count for this commander.
    total_decks: int = Field(ge=0)

    # A list of all tag rows found for this commander.
    # Each item in this list must match the CommanderTagRow model.
    tags: list[CommanderTagRow]

    # Timestamp for the overall commander scrape.
    scrape_timestamp: str

def utc_now_iso() -> str:
    """
    Return the current UTC time in ISO format.

    Why UTC?
    - GitHub Actions may run in a different timezone than your computer.
    - UTC avoids confusion when comparing weekly/monthly snapshots.
    - ISO format is easy to save in JSON and compare later.

    Example output:
    2026-05-06T14:30:22.123456+00:00
    """
    
    return datetime.now(timezone.utc).isoformat()