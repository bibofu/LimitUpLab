"""Market-universe rules for one-to-two relay predictions."""


CHINEXT_PREFIXES = ("300", "301")
BEIJING_PREFIXES = ("4", "8", "920")


def is_relay_research_security(symbol: str, name: str) -> bool:
    """Exclude risk-warning names and BSE stocks from relay inputs and context."""

    return (
        not symbol.startswith(BEIJING_PREFIXES)
        and "ST" not in name.upper()
        and "退" not in name
    )


def is_relay_candidate_symbol(symbol: str) -> bool:
    """Return whether a symbol belongs to the supported relay universe."""

    return not symbol.startswith((*CHINEXT_PREFIXES, *BEIJING_PREFIXES))
