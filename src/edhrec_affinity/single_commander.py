"""
single_commander.py

This file contains the first real scraper prototype for the EDHREC Commander
Tag Affinity Analysis project.

The goal is intentionally small:
- build one commander JSON URL from a known commander slug
- fetch the JSON payload from EDHREC
- inspect the JSON structure
- parse the commander's total deck count
- parse all normal commander tags from panels["taglinks"]
- save the parsed result as a local raw JSON file

This script does not yet:
- discover every commander
- scrape every commander
- handle cEDH special routes
- compute percentages, z-scores, ranks, or trends

Those steps will be included in other future files.
"""
import json
from pathlib import Path

# httpx is the HTTP client library.
# It lets Python send requests to URLs and receive responses
import httpx

from edhrec_affinity.models import CommanderScrapeResult, CommanderTagRow, utc_now_iso

# A user-agent identifies your script to the website.
#
# This is better than looking like a random anonymous bot.
# Later, you can make this more specific, for example:
# 'edhrec-affinity-project/0.1 contact:your-email@example.com"
#
# For now, keep it simple.
USER_AGENT = "edhrec-affinity-learning-project/0.1"

def build_commander_json_url(commander_slug: str) -> str:
    """
    Build the EDHREC JSON URL for a commander.

    Parameter:
    commander_slug:
        The EDHREC commander slug.

        Example:
        "jasmine-boreal-of-the-seven"

    Returns:
        A full URL string pointing to the expected JSON file.

    Why this is its own function:
        The JSON URL pattern might change later. By keeping URL construction
        in one place, we only need to update this function if that happens.
    """

    # Based on Chat 2 network inspection, commander JSON files appear
    # to be named after the commander slug.
    #
    # Example:
    # jasmine-boreal-of-the-seven.json
    # https://json.edhrec.com/pages/commanders/jasmine-boreal-of-the-seven.json
    return f"https://json.edhrec.com/pages/commanders/{commander_slug}.json"

def fetch_json(url: str) -> dict:
    """
    Fetch JSON data from a URL.

    Parameter:
    url:
        The full JSON URL to request.

    Returns:
        A Python dictionary created from the JSON response.

    Raises:
        httpx.HTTPStatusError:
            If the server returns an unsuccessful HTTP status code,
            such as 404, 403, or 500.

            httpx.TimeoutException:
                If the request takes too long.    
    
    Example:
        If the website returns this JSON:

        {
            "num_decks_avg": 5722,
            "taglinks": [...]
        }

        then this function returns it as a Python dict.
    """

    # Headers are extra information sent with the request.
    # Here, we send a User-Agent so the request identifies itself.
    headers = {"User-Agent": USER_AGENT}

    # httpx.Client is used to create a reusable HTTP client.
    #
    # headers=headers:
    #   Applies our User-Agent to requests made by this client.
    #
    # timeout=20.0:
    #   If the request takes too long, stop instead of hanging forever.
    #
    # follow_redirects=True:
    #   If the server redirects the request to another URL,
    #   HTTPX will follow that redirect automatically.
    with httpx.Client(headers=headers, timeout=20, follow_redirects=True) as client:
        # Send a GET rqeuest to the JSON URL.
        # GET means "retrieve data from this URL."
        response = client.get(url)

        # Raise an error if the response was unsuccessful.
        response.raise_for_status()

        # Convert the JSON response body into a Python dictionary.
        #
        # After this line, you can access fields like:
        # payload["num_decks_avg"]
        # payload["panels"]["taglinks"]
        payload = response.json()
        return payload

    
def inspect_top_level_keys(payload: dict) -> None:
    """
    Print the top-level keys in the JSON payload

    This was a temporary helper function that is no longer in use.
    It helps understand the real JSON structure before writing or changing
    parsing logic.
    """
    print("Top-level keys:")

    # sorted(...) prints the keys in alphabetical order, making the output
    # easier to scan.
    for key in sorted(payload.keys()):
        print(f" - {key}")

def parse_commander_payload(
        payload: dict,
        commander_slug: str,
        commander_name: str,
) -> CommanderScrapeResult:
    """
    Parse one commander JSON payload into the project's internal data shape.

    Parameters:
        payload:
            The full JSON dictionary returned by fetch_json().

        commander_slug:
            The EDHREC commander slug.

            Example:
            "jasmine-boreal-of-the-seven"

        commander_name:
            The human-readable commander name.

            Example:
            "Jasmine Boreal of the Seven"
    
    Returns:
        CommanderScrapeResult:
            A validated Pydantic object containing:
            - commander name
            - commander slug
            - total deck count
            - one normalized row per commander tag
            - scrape timestamp

    Important JSON paths confirmed in Chat 3:
        total decks:
            payload["num_decks_avg"]

        normal commander tags:
            payload["panels"]["taglinks"]

        each tag object:
            tag["count"]
            tag["slug"]
            tag["value"]
    """
    # Record when this scrape happened.
    #
    # Later, scrape timestamps will matter for weekly/monthly trend tracking.
    scrape_timestamp = utc_now_iso()

    # Total commander deck count is directly available at the top level.
    total_decks = int(payload["num_decks_avg"])

    # Tag data is inside payload["panels"]["taglinks"].
    # Each tag object has:
    # - count: number of decks for this commander-tag pair
    # - slug: stable tag identifier
    # - value: human-readable tag name
    taglinks = payload["panels"]["taglinks"]

    # This list will store one normalized row for eacht ag.
    tags: list[CommanderTagRow] = []

    # Convert every EDHREC tag object into our own consistent schema.
    for tag in taglinks:
        row = CommanderTagRow(
            commander_name=commander_name,
            commander_slug=commander_slug,
            total_decks=total_decks,
            tag_name=tag["value"],
            tag_slug=tag["slug"],
            tag_decks=int(tag["count"]),
            source_type="commander_json",
            scrape_timestamp=scrape_timestamp,
        )
        tags.append(row)

    # Return one object representing the full commander scrape result.
    return CommanderScrapeResult(
        commander_name=commander_name,
        commander_slug=commander_slug,
        total_decks=total_decks,
        tags=tags,
        scrape_timestamp=scrape_timestamp,
    )

def save_result(result: CommanderScrapeResult, output_path: Path) -> None:
    """
    Save a parsed commander scrape result as a JSON file.

    Parameters:
        result:
            The parsed and validated commander scrape result.

        output_path:
            Where the JSON file should be written.

            Example:
            Path("data/raw/single_commander/jasmine-boreal-of-the-seven.json")

    Why this matters:
        Later, the full pipeline will preserve dated raw snapshots instead of
        overwriting old data. This function is the first small version of that
        storage pattern.
    """
    
    # Create the parent folder if it does not already exist.
    #
    # Example:
    # If output_path is:
    # data/raw/single_commander/jasmine-boreal-of-the-seven.json
    #
    # then output_path.parent is:
    # data/raw/single_commander
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Open the file for writing using UTF-8 so commander names and tag names
    # with special characters are handled safely.
    with output_path.open("w", encoding="utf-8") as f:
        # result.model_dump() converts the pydantic model into a regular
        # Python dictionary that json.dump() can write to disk.
        #
        # indent=2 makes the file readable.
        # ensure_ascii=False preserves non-ASCII characters instead of escaping them.
        json.dump(result.model_dump(), f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    slug = "jasmine-boreal-of-the-seven"
    name = "Jasmine Boreal of the Seven"

    # Step 1: Build the JSON URL.
    url = build_commander_json_url(slug)

    # Step 2: Fetch the JSON payload.
    payload = fetch_json(url)

    # Step 3: Parse the payload into the project data model.
    result = parse_commander_payload(
        payload=payload,
        commander_slug=slug,
        commander_name=name,
    )

    # Step 4: Save the parsed result locally.
    save_result(
        result,
        Path("data/raw/single_commander/jasmine-boreal-of-the-seven.json"),
    )

    # Step 5: Print a small confirmation message.
    print(f"Saved {len(result.tags)} tags for {result.commander_name}")