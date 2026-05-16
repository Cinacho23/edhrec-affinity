# Import httpx so Python can request/download a webpage.
# We use it here to fetch one EDHREC commander page.
import httpx

# Import BeautifulSoup so we can parse the HTML from the webpage.
# HTML is the structure behind normal websites.
from bs4 import BeautifulSoup


# This is the commander page we want to inspect.
# Jasmine Boreal of the Seven is useful because we already suspect
# that her page may show only some tags at first.
url = "https://edhrec.com/commanders/jasmine-boreal-of-the-seven"

# Headers are extra information sent with the request.
# The User-Agent identifies the script in a more transparent way.
headers = {
    "User-Agent": "EDHREC Tag Affinity Research Project - personal analysis"
}

try:
    # Download the HTML from the commander page.
    # timeout=30 means the request will stop after 30 seconds if the page does not respond.
    response = httpx.get(url, headers=headers, timeout=30)

    # Raise an error if the status code is bad.
    # For example: 403, 404, or 500.
    response.raise_for_status()

except httpx.HTTPError as error:
    # If the request fails, print the problem
    # Later int he proejct, this should be saved to a failure log.
    print("Request failed:", error)

else:
    # This only runs if the request worked.

    # Get the HTML from the successful response.
    html = response.text

    # Parse the downloaded HTML using BeautifulSoup.
    # "html.parser" tells BeautifulSoup to treat the text as HTML.
    soup = BeautifulSoup(html, "html.parser")

    # Extract the visible text from the parsed HTML.
    #
    # get_text("\n", strip=True) means:
    # - Use "\n" as the separator between pieces of text.
    # - strip=True removes extra whitespace from the beginning/end of each text piece.
    #
    # This gives us a large plain-text version of the page.
    text = soup.get_text("\n", strip=True)

    # Split the large text block into separate lines.
    # This makes it easier to inspect one line at a time.
    for line in text.splitlines():

        # Print lines that seem relevant to our validation test.
        #
        # "decks" in line catches lines like:
        # "5,695 decks"
        # "442 decks"
        #
        # The second part checks for known visible tag names.
        # This is only a quick prototype, not a final scraper
        #if "decks" in line or line in ("Tags", "Power", "Vanilla", "Aggro", "Tokens"):
        if line in ("Tags", "Power", "Vanilla", "Aggro", "Tokens"):
            print(line)