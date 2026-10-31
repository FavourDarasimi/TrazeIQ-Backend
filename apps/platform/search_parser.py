"""Tokenized search query parser and ORM filter builder.

Supports:
- key:value (e.g. status:open, severity:critical, env:production, user:123)
- is:status / is:severity / is:assigned / is:unassigned
- Quoted values (e.g. service:"payment gateway", metadata.os:"Mac OS X")
- Negation prefix with '-' or '!' (e.g. -status:resolved, -severity:low, -env:dev)
- Metadata / tag key lookups (e.g. metadata.browser:Chrome, metadata.release:v1.2.0)
- Free-text terms (e.g. "connection timeout", DatabaseError)
- Whitelist mapping to prevent arbitrary model attribute lookups.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional
from uuid import UUID

from django.db.models import Q


@dataclass
class SearchToken:
    key: Optional[str]
    value: str
    negated: bool = False
    raw: str = ""

    def __repr__(self) -> str:
        prefix = "-" if self.negated else ""
        if self.key:
            return f"{prefix}{self.key}:{self.value}"
        return f"{prefix}{self.value}"


# Regex matching:
# 1. Optional negation: (-|!)?
# 2. Optional key: ([\w.-]+):
# 3. Quoted string ("..." or '...') or non-space value (\S+)
_TOKEN_PATTERN = re.compile(
    r"(?P<neg>[-!])?"
    r"(?:(?P<key>[\w.-]+):)?"
    r"(?:\"(?P<qval>[^\"]*)\"|\x27(?P<sqval>[^\x27]*)\x27|(?P<rawval>\S+))"
)


def parse_search_query(query: str | None) -> list[SearchToken]:
    """Parse a search query string into a list of SearchToken instances."""
    if not query or not query.strip():
        return []

    tokens: list[SearchToken] = []
    for match in _TOKEN_PATTERN.finditer(query.strip()):
        neg = bool(match.group("neg"))
        raw_key = match.group("key")
        key = raw_key.lower().strip() if raw_key else None
        val = (
            match.group("qval")
            if match.group("qval") is not None
            else (
                match.group("sqval")
                if match.group("sqval") is not None
                else match.group("rawval")
            )
        )
        if val is None:
            continue
        tokens.append(
            SearchToken(
                key=key,
                value=val,
                negated=neg,
                raw=match.group(0),
            )
        )
    return tokens


def _sanitize_meta_key(key: str) -> str:
    """Sanitize arbitrary JSON keys to prevent ORM injection."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "", key.strip())


# Whitelisted status and severity values
_INCIDENT_STATUSES = {"open", "investigating", "resolved", "ignored"}
_INCIDENT_SEVERITIES = {"critical", "high", "medium", "low"}
_EVENT_LEVELS = {"fatal", "error", "warning", "info", "debug"}

_EMPTY_MATCH_Q = Q(pk__in=[])


def build_incident_search_q(query: str | None) -> Q:
    """Convert a tokenized query string into a Django Q filter for Incident."""
    if not query or not query.strip():
        return Q()

    # If the user typed a non-empty search query that contains no alphanumeric
    # characters (e.g. punctuation-only queries like "!@#$%^&*()"), return an empty match
    # so that it does not falsely match all records or invert to an unfiltered match.
    if not re.search(r"[a-zA-Z0-9]", query):
        return _EMPTY_MATCH_Q

    tokens = parse_search_query(query)
    if not tokens:
        return _EMPTY_MATCH_Q

    root_q = Q()

    for token in tokens:
        token_q: Optional[Q] = None
        val = token.value.strip()

        if token.key == "is":
            low_val = val.lower()
            if low_val in _INCIDENT_STATUSES:
                token_q = Q(status=low_val)
            elif low_val in _INCIDENT_SEVERITIES:
                token_q = Q(severity=low_val)
            elif low_val == "assigned":
                token_q = Q(assigned_to__isnull=False)
            elif low_val == "unassigned":
                token_q = Q(assigned_to__isnull=True)
            else:
                # Treat unrecognized is: as free text
                token_q = _incident_free_text_q(token.raw)

        elif token.key in ("status", "state"):
            low_val = val.lower()
            if low_val in _INCIDENT_STATUSES:
                token_q = Q(status=low_val)
            else:
                token_q = Q(status__iexact=val)

        elif token.key in ("severity", "sev"):
            low_val = val.lower()
            if low_val in _INCIDENT_SEVERITIES:
                token_q = Q(severity=low_val)
            else:
                token_q = Q(severity__iexact=val)

        elif token.key in ("env", "environment"):
            token_q = Q(error_group__events__environment__iexact=val)

        elif token.key in ("user", "user_id"):
            token_q = Q(error_group__events__user_id__icontains=val)

        elif token.key == "service":
            token_q = Q(error_group__events__service__icontains=val)

        elif token.key == "endpoint":
            token_q = Q(error_group__events__endpoint__icontains=val)

        elif token.key == "fingerprint":
            token_q = Q(error_group__fingerprint__iexact=val)

        elif token.key in ("release", "version"):
            token_q = (
                Q(error_group__events__metadata__release__icontains=val)
                | Q(error_group__events__metadata__version__icontains=val)
                | Q(error_group__events__metadata__app_version__icontains=val)
            )

        elif token.key in ("assigned", "assigned_to", "assignee"):
            token_q = (
                Q(assigned_to__username__iexact=val)
                | Q(assigned_to__email__iexact=val)
            )

        elif token.key == "project":
            try:
                project_uuid = UUID(val)
                token_q = Q(project_id=project_uuid)
            except ValueError:
                token_q = Q(project__name__icontains=val)

        elif token.key and (
            token.key.startswith("metadata.")
            or token.key.startswith("meta.")
            or token.key.startswith("tag.")
        ):
            _, _, subkey = token.key.partition(".")
            clean_sub = _sanitize_meta_key(subkey)
            if clean_sub:
                token_q = (
                    Q(**{f"error_group__events__metadata__{clean_sub}__icontains": val})
                    | Q(**{f"error_group__events__metadata__tags__{clean_sub}__icontains": val})
                )
            else:
                token_q = _incident_free_text_q(token.raw)

        elif token.key is not None:
            # Unrecognized key: treat as literal free text matching title/message
            token_q = _incident_free_text_q(token.raw)

        else:
            # Free-text token (no key:)
            token_q = _incident_free_text_q(val)

        if token_q is not None:
            if token.negated:
                root_q &= ~token_q
            else:
                root_q &= token_q

    return root_q


def _incident_free_text_q(term: str) -> Q:
    """Free text substring match across incident & event fields."""
    return (
        Q(error_group__title__icontains=term)
        | Q(error_group__events__message__icontains=term)
        | Q(error_group__events__service__icontains=term)
        | Q(error_group__events__endpoint__icontains=term)
    )


def build_event_search_q(query: str | None) -> Q:
    """Convert a tokenized query string into a Django Q filter for Event."""
    if not query or not query.strip():
        return Q()

    # If the user typed a non-empty search query that contains no alphanumeric
    # characters (e.g. punctuation-only queries like "!@#$%^&*()"), return an empty match
    # so that it does not falsely match all records or invert to an unfiltered match.
    if not re.search(r"[a-zA-Z0-9]", query):
        return _EMPTY_MATCH_Q

    tokens = parse_search_query(query)
    if not tokens:
        return _EMPTY_MATCH_Q

    root_q = Q()

    for token in tokens:
        token_q: Optional[Q] = None
        val = token.value.strip()

        if token.key == "is":
            low_val = val.lower()
            if low_val in _EVENT_LEVELS:
                token_q = Q(level=low_val)
            else:
                token_q = _event_free_text_q(token.raw)

        elif token.key in ("level", "sev", "severity"):
            low_val = val.lower()
            if low_val in _EVENT_LEVELS:
                token_q = Q(level=low_val)
            else:
                token_q = Q(level__iexact=val)

        elif token.key in ("env", "environment"):
            token_q = Q(environment__iexact=val)

        elif token.key in ("user", "user_id"):
            token_q = Q(user_id__icontains=val)

        elif token.key == "service":
            token_q = Q(service__icontains=val)

        elif token.key == "endpoint":
            token_q = Q(endpoint__icontains=val)

        elif token.key == "fingerprint":
            token_q = Q(fingerprint__iexact=val)

        elif token.key in ("release", "version"):
            token_q = (
                Q(metadata__release__icontains=val)
                | Q(metadata__version__icontains=val)
                | Q(metadata__app_version__icontains=val)
            )

        elif token.key == "project":
            try:
                project_uuid = UUID(val)
                token_q = Q(project_id=project_uuid)
            except ValueError:
                token_q = Q(project__name__icontains=val)

        elif token.key and (
            token.key.startswith("metadata.")
            or token.key.startswith("meta.")
            or token.key.startswith("tag.")
        ):
            _, _, subkey = token.key.partition(".")
            clean_sub = _sanitize_meta_key(subkey)
            if clean_sub:
                token_q = (
                    Q(**{f"metadata__{clean_sub}__icontains": val})
                    | Q(**{f"metadata__tags__{clean_sub}__icontains": val})
                )
            else:
                token_q = _event_free_text_q(token.raw)

        elif token.key is not None:
            token_q = _event_free_text_q(token.raw)

        else:
            token_q = _event_free_text_q(val)

        if token_q is not None:
            if token.negated:
                root_q &= ~token_q
            else:
                root_q &= token_q

    return root_q


def _event_free_text_q(term: str) -> Q:
    """Free text substring match across event fields."""
    return (
        Q(message__icontains=term)
        | Q(fingerprint__icontains=term)
        | Q(service__icontains=term)
        | Q(environment__icontains=term)
        | Q(endpoint__icontains=term)
        | Q(user_id__icontains=term)
    )
