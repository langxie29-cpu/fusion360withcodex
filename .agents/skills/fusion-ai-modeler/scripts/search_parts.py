#!/usr/bin/env python3
"""Search the bundled, explicitly qualified mechanical-interface catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "assets" / "parts-catalog.json"


def normalize(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def score(record: dict, query: str) -> int:
    needle = normalize(query)
    if not needle:
        return 0
    values = [record.get("id", ""), record.get("name", ""), *record.get("aliases", [])]
    normalized = [normalize(value) for value in values]
    if needle in normalized:
        return 100
    if any(needle in value or value in needle for value in normalized):
        return 70
    tokens = [normalize(token) for token in query.split() if normalize(token)]
    return sum(10 for token in tokens if any(token in value for value in normalized))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Part family, alias, or exact part number")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    ranked = sorted(
        ((score(record, args.query), record) for record in catalog["records"]),
        key=lambda item: (-item[0], item[1]["id"]),
    )
    matches = [record for record_score, record in ranked if record_score > 0][: max(1, args.limit)]
    result = {
        "query": args.query,
        "matches": matches,
        "warning": "generic_unverified records are candidates only; confirm an exact manufacturer part number and primary datasheet.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if matches else 1


if __name__ == "__main__":
    sys.exit(main())
