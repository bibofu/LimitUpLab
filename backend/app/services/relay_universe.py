"""Market-universe rules for one-to-two relay predictions."""


CHINEXT_PREFIXES = ("300", "301")


def is_relay_candidate_symbol(symbol: str) -> bool:
    """Return whether a symbol belongs to the supported relay universe."""

    return not symbol.startswith(CHINEXT_PREFIXES)
