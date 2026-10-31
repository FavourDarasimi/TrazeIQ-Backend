import hashlib
import re

# ---- Secret redaction (Agent.md rule 5: error content is untrusted input) ----

_SECRET_KEY_RE = re.compile(r"(?i)\bsecret\s*_?\s*key\s*=\s*['\"]?[^\s'\"]+")
_DATABASE_URL_RE = re.compile(r"(?i)\bdatabase\s*_?\s*url\s*=\s*['\"]?[^\s'\"]+")
# Quotes around the value are optional (bare password=hunter2 is the common
# accident) — like SECRET_KEY/DATABASE_URL above. The value stops at
# whitespace, quotes, commas or semicolons so trailing delimiters survive.
_PASSWORD_RE = re.compile(r"(?i)(password\s*[:=]\s*['\"]?)([^'\"\s;,]+)(['\"]?)")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")
_GENERIC_CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|client[_-]?secret)\s*[:=]\s*['\"]?[^\s'\"]+"
)

_REDACTIONS = [
    (_SECRET_KEY_RE, "SECRET_KEY=[REDACTED]"),
    (_DATABASE_URL_RE, "DATABASE_URL=[REDACTED]"),
    (_PASSWORD_RE, r"\1[REDACTED]\3"),
    (_BEARER_RE, "Bearer [REDACTED]"),
    (_JWT_RE, "[REDACTED-JWT]"),
    (_GENERIC_CREDENTIAL_RE, r"\1=[REDACTED]"),
]


def redact_secrets(text: str) -> str:
    """Scrub likely secrets from error content before it is stored or analyzed.

    Runs before anything touches the DB or (later) the AI prompt — a
    monitoring tool must never become the leak vector for credentials that
    were accidentally logged into a stacktrace.
    """
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


# ---- Fingerprinting (Agent.md rule 3: dedup before you store) ----

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]+")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
_LINE_COL_RE = re.compile(r":\s*\d+(?::\s*\d+)?")
# Python-style "line 21" / "line=21" / "line #21" — the ":N" rule above only
# covers "file.py:21" style, so identical tracebacks from different call-site
# lines (line 21 vs line 31) split into separate groups without this.
_LINE_WORD_RE = re.compile(r"(?i)\bline\s*[#:=]?\s*\d+")
_WS_RE = re.compile(r"\s+")
# Probe/test header and its variable suffix (e.g. wallet-probe-2-1788031896)
# must not create separate fingerprints — strip the whole header value.
_PROBE_HEADER_RE = re.compile(r"\s+X-Traze-Test=\S+", re.IGNORECASE)

_CHUNK_HASH_RE = re.compile(
    r"([a-zA-Z0-9_\-]+?)-[a-f0-9]{5,64}(\.(?:js|mjs|cjs|css|map))(?:\?.*)?$",
    re.IGNORECASE,
)

_VENDOR_PATTERNS = [
    # node_modules
    re.compile(r"(?:^|[\\/])node_modules[\\/]"),
    # Python site-packages / dist-packages
    re.compile(r"(?:^|[\\/])(?:site-packages|dist-packages)[\\/]"),
    # Python stdlib
    re.compile(r"(?:^|[\\/])lib[\\/]python\d+\.\d+[\\/]"),
    re.compile(r"^<frozen\s+", re.IGNORECASE),
    re.compile(r"^<built-in\s+", re.IGNORECASE),
    # Node.js stdlib
    re.compile(r"^node:"),
    re.compile(r"(?:^|[\\/])node:internal[\\/]"),
    # Next.js internals and chunks
    re.compile(r"(?:^|[\\/])next[\\/]dist[\\/]"),
    re.compile(r"(?:^|[\\/])\.(?:next|output)[\\/]"),
    re.compile(r"(?:^|[\\/])_next[\\/]static[\\/]chunks[\\/]"),
    re.compile(
        r"(?:^|[\\/])chunks[\\/](?:main|framework|webpack|app-pages-internals|[0-9]+-[a-f0-9]+)"
    ),
]

PY_FRAME_RE = re.compile(
    r'^\s*File\s+["\']([^"\']+)["\'],\s*(?:line\s+(\d+))?(?:,\s*in\s+(.+))?',
    re.IGNORECASE,
)
JS_FRAME_RE = re.compile(
    r'^\s*at\s+(?:(.+?)\s+\((.+?)\)|(.+))',
    re.IGNORECASE,
)


def clean_protocol(path: str) -> str:
    """Strip bundler protocol prefixes such as webpack-internal:/// or webpack://."""
    path = re.sub(r"^webpack-internal:\/\/\/?(?:\([^\)]+\)\/)?", "", path)
    path = re.sub(r"^webpack:\/\/\/?(?:[^\/]+\/)?", "", path)
    return path


def normalize_chunk_filename(path: str) -> str:
    """Normalize filenames by stripping protocol wrappers, query parameters,
    and chunk content hashes (e.g. main-4f81b.js -> main.js).
    """
    if not path:
        return ""
    path = path.split("?")[0].replace("\\", "/")
    path = clean_protocol(path)
    return _CHUNK_HASH_RE.sub(r"\1\2", path)


def is_in_app_frame(filename: str = "", frame_text: str = "") -> bool:
    """Determine whether a stack frame belongs to user application code or
    runtime / vendor / framework code (node_modules, site-packages, stdlib,
    Next.js chunks).
    """
    clean_fn = clean_protocol(filename or "").replace("\\", "/")
    clean_txt = clean_protocol(frame_text or "").replace("\\", "/")
    for pat in _VENDOR_PATTERNS:
        if clean_fn and pat.search(clean_fn):
            return False
        if clean_txt and pat.search(clean_txt):
            return False
    return True


def parse_stacktrace_frames(stacktrace: str) -> list[dict]:
    """Parse a stacktrace string into a structured list of frame dictionaries."""
    frames: list[dict] = []
    lines = (stacktrace or "").splitlines()
    has_py_frames = any(PY_FRAME_RE.match(line.strip()) for line in lines)
    has_js_frames = any(JS_FRAME_RE.match(line.strip()) for line in lines)

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("Traceback (") or "<module>" in s.lower():
            continue

        # Python frame: File "...", line 123, in foo
        m_py = PY_FRAME_RE.match(s)
        if m_py:
            fn, lno, func = m_py.groups()
            fn_clean = normalize_chunk_filename(fn)
            frames.append({
                "filename": fn_clean,
                "function": func or "",
                "lineno": int(lno) if lno and lno.isdigit() else None,
                "in_app": is_in_app_frame(filename=fn),
                "raw": s,
            })
            continue

        # JavaScript frame: at foo (path:line:col) or at path:line:col
        m_js = JS_FRAME_RE.match(s)
        if m_js:
            func1, loc1, loc2 = m_js.groups()
            func = func1 or ""
            raw_loc = loc1 or loc2 or ""
            # Strip :line:col
            loc_without_line = re.sub(
                r":\d+(?::\d+)?(?=[\)\s]|$)", "", raw_loc
            ).strip()
            fn_clean = normalize_chunk_filename(loc_without_line)
            frames.append({
                "filename": fn_clean,
                "function": func,
                "lineno": None,
                "in_app": is_in_app_frame(filename=loc_without_line),
                "raw": s,
            })
            continue

        # If the traceback is recognized as Python or JS, skip source code lines or error headers
        if has_py_frames or has_js_frames:
            continue

        # Generic line fallback (strip line numbers and check in_app)
        clean_line = re.sub(r":\d+(?::\d+)?(?=[\)\s]|$)", "", s)
        clean_line = re.sub(
            r",?\s*\bline\s*[#:=]?\s*\d+", "", clean_line, flags=re.IGNORECASE
        )
        norm_line = normalize_chunk_filename(clean_line)
        frames.append({
            "filename": norm_line,
            "function": "",
            "lineno": None,
            "in_app": is_in_app_frame(filename=clean_line, frame_text=s),
            "raw": s,
        })
    return frames


def _normalize(text: str) -> str:
    text = _ADDRESS_RE.sub("0xADDR", text)
    text = _UUID_RE.sub("UUID", text)
    text = _PROBE_HEADER_RE.sub("", text)
    # Don't apply numeric-suffix stripping to the path-fingerprint itself;
    # the X-Traze-Test strip already handles the wallet-probe-2-... case.
    text = _LINE_COL_RE.sub(":N", text)
    text = _LINE_WORD_RE.sub("line N", text)
    text = _WS_RE.sub(" ", text)
    return text.strip().lower()


def fingerprint(
    *,
    message: str,
    stacktrace: str = "",
    frames: list[dict] | None = None,
    version: int = 2,
    top_n: int = 5,
) -> str:
    """Deterministic signature for one error pattern.

    Supports algorithm version 1 (legacy: top 5 raw frames normalized with :N)
    and version 2 (in-app frame filtering, chunk hash normalization, line-number
    immunity, and fallback to all frames if none are in-app).
    """
    if version == 1:
        raw_frames = [
            line.strip()
            for line in (stacktrace or "").splitlines()
            if line.strip() and "<module>" not in line.lower()
        ]
        parts = [_normalize(message.splitlines()[0])] if message else []
        parts += [_normalize(frame) for frame in raw_frames[:5]]
        payload = "\n".join(parts)
        return hashlib.sha256(payload.encode()).hexdigest()

    # Version 2: in-app frame filtering, chunk hash stripping, line number omission
    parsed_frames: list[dict] = []
    if frames:
        for f in frames:
            fn = normalize_chunk_filename(f.get("filename", ""))
            func = f.get("function", "")
            in_app = f.get("in_app")
            if in_app is None:
                in_app = is_in_app_frame(filename=fn)
            parsed_frames.append({
                "filename": fn,
                "function": func,
                "in_app": bool(in_app),
            })
    elif stacktrace:
        parsed_frames = parse_stacktrace_frames(stacktrace)

    in_app_frames = [f for f in parsed_frames if f["in_app"]]
    selected_frames = (
        in_app_frames[:top_n] if in_app_frames else parsed_frames[:top_n]
    )

    parts = []
    if message:
        first_line_msg = _normalize(message.splitlines()[0])
        if first_line_msg:
            parts.append(first_line_msg)

    for f in selected_frames:
        fn = _normalize(f["filename"])
        func = _normalize(f["function"])
        if func and fn:
            parts.append(f"{fn} in {func}")
        elif fn:
            parts.append(fn)
        elif func:
            parts.append(func)

    payload = "\n".join(parts)
    return hashlib.sha256(payload.encode()).hexdigest()


def first_line(text: str) -> str:
    """The message line used as the ErrorGroup title."""
    line = text.splitlines()[0] if text else ""
    return line[:255]


def severity_from_level(level: str) -> str:
    """Map an event ``level`` to the incident ``severity`` scale."""
    return {
        "fatal": "critical",
        "error": "high",
        "warning": "medium",
        "info": "low",
        "debug": "low",
    }.get(level, "medium")


def level_from_severity(severity: str) -> str:
    """Map an incident ``severity`` back to the equivalent event ``level``.

    One-to-one inverse of :func:`severity_from_level` — used for the
    ``?severity=`` filter on the events list.
    """
    return {
        "critical": "fatal",
        "high": "error",
        "medium": "warning",
        "low": "info",
    }.get(severity, severity)