"""Strict parsing of the model's response into the analysis shape.

The model's text is untrusted input too: a crafted stacktrace may try to
change the output shape. Parsing therefore ignores everything outside the
first JSON object and accepts only the exact ``{"root_cause",
"suggested_fix", "confidence"}`` shape — anything else is a parse failure,
which triggers the strict-reminder retry and then a graceful ``failed``
status instead of a crash.
"""

import json
import re

CONFIDENCE_CHOICES = {"low", "medium", "high"}

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_analysis_json(text: str) -> dict | None:
    """Extract and validate one analysis object from the model's response.

    Returns ``{"root_cause", "suggested_fix", "confidence"}`` on success, or
    ``None`` when the text is not a valid object in the required shape
    (garbage prefix, missing/empty fields, unknown confidence, trailing
    prose after the object are all tolerated as long as the object itself is
    strict).
    """
    match = _JSON_OBJECT_RE.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    root_cause = data.get("root_cause")
    suggested_fix = data.get("suggested_fix")
    confidence = data.get("confidence")
    if not isinstance(root_cause, str) or not root_cause.strip():
        return None
    if not isinstance(suggested_fix, str) or not suggested_fix.strip():
        return None
    if confidence not in CONFIDENCE_CHOICES:
        return None
    return {
        "root_cause": root_cause.strip(),
        "suggested_fix": suggested_fix.strip(),
        "confidence": confidence,
    }
