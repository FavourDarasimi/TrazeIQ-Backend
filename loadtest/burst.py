#!/usr/bin/env python3
"""Phase 5E — burst load test for ``POST /api/v1/events/`` (stdlib only).

Simulates a crash loop: N identical errors from M concurrent threads, then
asserts the two Phase 5E DoD properties:

1. p95 ingestion latency stays under ``--p95-budget-ms`` regardless of burst
   size (ingestion must respond in milliseconds — Agent.md rule 1), and
2. the burst produces exactly one ``ErrorGroup`` and one open ``Incident``
   (dedup holds under concurrent writes — the ``(project, fingerprint)``
   unique constraint plus the ``IntegrityError`` fallback in
   ``apps/events/services.py``).

The dedup check needs a JWT (``--jwt``); without it the script still reports
latencies but skips verification. Get one via::

    curl -s -X POST $BASE/api/v1/auth/login/ \\
      -H 'Content-Type: application/json' \\
      -d '{"email":"you@example.com","password":"..."}'

Example::

    python burst.py --base-url http://localhost:8000 \\
        --api-key <project-api-key> --jwt <jwt> \\
        --count 500 --concurrency 20 --p95-budget-ms 500

Exit code is 0 only when every assertion holds.
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_MESSAGE = "LoadTestError: burst probe"


def _post_event(base_url, api_key, payload):
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{base_url}/api/v1/events/",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
        method="POST",
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
            return (time.monotonic() - start) * 1000, response.status, ""
    except urllib.error.HTTPError as exc:
        return (time.monotonic() - start) * 1000, exc.code, exc.read()[:200].decode(
            "utf-8", "replace"
        )


def _get_json(url, jwt):
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {jwt}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(pct / 100 * len(ordered)), len(ordered) - 1)
    return ordered[index]


def main():  # noqa: C901 — linear orchestration, kept in one place on purpose
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--jwt", default="")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--service", default="loadtest")
    parser.add_argument("--p95-budget-ms", type=float, default=1000.0)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    # A unique marker per run so the dedup check isolates this burst.
    marker = f"{args.message} [{time.time_ns()}]"
    payload = {
        "message": marker,
        "stacktrace": "Traceback (most recent call last):\n  File burst.py in probe",
        "level": "error",
        "environment": "loadtest",
        "service": args.service,
    }

    print(
        f"burst: {args.count} events x {args.concurrency} threads -> {base_url}"
    )
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(
            pool.map(
                lambda _: _post_event(base_url, args.api_key, payload),
                range(args.count),
            )
        )

    latencies = [lat for lat, _, _ in results]
    failures = [(code, body) for _, code, body in results if code != 201]
    p50 = statistics.median(latencies)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    print(f"latency ms: p50={p50:.1f} p95={p95:.1f} p99={p99:.1f} "
          f"max={max(latencies):.1f}")
    print(f"failures: {len(failures)}/{args.count}")
    for code, body in failures[:5]:
        print(f"  {code}: {body}")

    ok = True
    if failures:
        print("FAIL: non-201 responses during burst")
        ok = False
    if p95 > args.p95_budget_ms:
        print(f"FAIL: p95 {p95:.1f}ms exceeds budget {args.p95_budget_ms:.1f}ms")
        ok = False

    if args.jwt:
        time.sleep(1)  # let the last writes commit before verifying
        data = _get_json(
            f"{base_url}/api/v1/incidents/?search={urllib.parse.quote(marker)}",
            args.jwt,
        )
        incidents = data["data"]["incidents"]
        counts = {inc["error_group"]["count"] for inc in incidents}
        print(f"dedup: {len(incidents)} incident(s), counts={sorted(counts)}")
        if len(incidents) != 1:
            print(f"FAIL: expected 1 incident, got {len(incidents)}")
            ok = False
        elif incidents[0]["error_group"]["count"] != args.count:
            print(
                f"FAIL: expected count={args.count}, "
                f"got {incidents[0]['error_group']['count']}"
            )
            ok = False
    else:
        print("dedup check skipped (no --jwt supplied)")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
