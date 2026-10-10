"""Exercise public health and authenticated boundary of deployed AuraFit.

Run: python scripts/smoke.py http://127.0.0.1:8080
"""
from __future__ import annotations
import json
import sys
import urllib.error
import urllib.request


def probe(base: str, path: str, expected_status: int, *, auth: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    request = urllib.request.Request(base.rstrip("/") + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    assert status == expected_status, f"{path}: expected {expected_status}, got {status}"
    data = json.loads(payload)
    print(f"PASS {path}: HTTP {status}")
    return data


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
    assert probe(base, "/health/live", 200)["status"] == "alive"
    assert probe(base, "/health/ready", 200)["status"] == "ready"
    assert "error" in probe(base, "/metrics/allostatic-load", 401)
    assert "error" in probe(base, "/metrics/allostatic-load", 401, auth="invalid")
    print("Smoke checks passed (database reads and writes require registered users and integration fixtures).")


if __name__ == "__main__":
    main()
