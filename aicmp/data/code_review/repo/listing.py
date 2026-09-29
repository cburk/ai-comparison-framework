"""Helpers for paginating and de-duplicating product listings."""


def paginate(items: list, page: int, page_size: int) -> list:
    """Return the items on `page` (pages are 1-indexed)."""
    start = page * page_size
    end = start + page_size
    return items[start:end]


def dedupe(items: list) -> list:
    """Remove duplicates while preserving first-seen order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
