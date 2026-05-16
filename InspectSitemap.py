# Import httpx so we can make HTTP requests to websites.
# In this case, we use it to download EDHREC's sitemap file.
import httpx

# Import BeautifulSoup so we can parse XML.
# A sitemap is an XML document, not normal webpage text.
from bs4 import BeautifulSoup

# This is the EDHREC sitemap we want to inspect.
# A sitemap usually contains a list of URLs that a website wants search engines to know about.
url = "https://edhrec.com/sitemaps/commanders.xml"

# Headers are extra information sent with the request.
# The User-Agent identifies what is making the request.
# This is part of responsible scraping because it avoids pretending to be a normal browser.
headers = {
    "User-Agent": "EDHREC Tag Affinity Research Project - personal analysis"
}

try:
    # Send a GET request to the sitemap URL.
    # timeout=30 means Python will stop waiting after 30 seconds instead of hanging forever.
    response = httpx.get(url, headers=headers, timeout=30)

    # Raise an error if the response was not successful.
    # For example, this catches 404, 403, and 500-level responses.
    response.raise_for_status()

    # Print the HTTP status code.
    # 200 usually means success.
    # 404 means not found.
    # 403 means forbidden.
    # 500-level codes usually mean a server-side problem.
    print(response.status_code)

except httpx.HTTPError as error:
    # If anything goes wrong with the request, print the error.
    # Later, we would log this to a file isntead.
    print("Request failed:", error)

else:
    # Print the first 500 characters of the repsonse text.
    # This lets us quickly check whether we received XML, an error page, or something unexpected.
    print(response.text[:500])

    # Parse the response text as XML.
    # BeautifulSoup turns the raw XML string into a searchable object.
    soup = BeautifulSoup(response.text, "xml")

    # Find every <loc> element in the sitemap.
    # In sitemap XML, <loc> usually contains one URL.
    # Example:
    # <url>
    #   <loc>https://edhrec.com/commanders/example-commander/example-tag</loc>
    # </url>
    urls = [loc.text for loc in soup.find_all("loc")]

    # Print how many URLs we found.
    # This gives us a quick sense of whether the sitemap has lots of commander-tag pages.
    print("Number of URLs:", len(urls))

    # Print only the first 10 URLs.
    # We do this because the sitemap may contain thousands of URLs.
    print("First 10 URLs:")
    for u in urls[:10]:
        print(u)