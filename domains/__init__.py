"""Domain registry — static map of available domains."""

from domains.base import Domain


def get_domain(name: str) -> Domain:
    """Get a domain by name. Lazy import to avoid loading all domains on startup."""
    if name == "finance":
        from domains.finance import FinanceDomain
        return FinanceDomain()
    elif name == "fda":
        from domains.fda import FDADomain
        return FDADomain()
    else:
        raise ValueError(f"Unknown domain: {name!r}. Available: {list_domains()}")


def list_domains() -> list[str]:
    return ["finance", "fda"]
