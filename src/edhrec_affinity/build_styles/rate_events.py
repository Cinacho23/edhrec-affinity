"""Dependency-free recognition of confirmed provider rate-limit receipts."""


def confirmed_rate_stop(stop):
    """Only an explicit 429 or the exact recorded EDHREC rate-limit route."""
    if not isinstance(stop, dict) or stop.get("reason") != "rate_limit":
        return False
    if stop.get("status_code") == 429:
        return stop.get("provider") in {"edhrec", "scryfall"}
    return (stop.get("provider") == "edhrec" and stop.get("status_code") == 302
            and stop.get("rate_limit_redirect") is True
            and stop.get("redirect_target") == "https://edhrec.com/rate-limit")
