"""Shared pagination for the platform list endpoints.

Same contract as the tenant event list: ``page``/``page_size`` query params,
max 100 rows per page — an unbounded admin list is a latency and memory bomb
the day the user table grows.
"""

import math

from rest_framework import serializers


DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def parse_pagination(query) -> tuple[int, int]:
    """Parse ``page``/``page_size``, raising DRF validation on bad values."""
    errors: dict[str, str] = {}
    try:
        page = int(query.get("page", "1"))
        if page < 1:
            errors["page"] = "Must be a positive integer."
    except (TypeError, ValueError):
        errors["page"] = "Must be a positive integer."
    try:
        page_size = int(query.get("page_size", str(DEFAULT_PAGE_SIZE)))
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            errors["page_size"] = (
                f"Must be an integer between 1 and {MAX_PAGE_SIZE}."
            )
    except (TypeError, ValueError):
        errors["page_size"] = (
            f"Must be an integer between 1 and {MAX_PAGE_SIZE}."
        )
    if errors:
        raise serializers.ValidationError(errors)
    return page, page_size


def paginate(queryset, page: int, page_size: int) -> tuple[list, dict]:
    """Slice one page plus the ``pagination`` envelope metadata."""
    total = queryset.count()
    start = (page - 1) * page_size
    return list(queryset[start : start + page_size]), {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
        "has_next": start + page_size < total,
        "has_previous": page > 1,
    }
