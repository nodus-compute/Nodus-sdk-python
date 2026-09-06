"""Copy or check the public contract from a sibling Nodus checkout."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nodus_checkout", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = args.nodus_checkout / "design" / "openapi.yaml"
    target = Path(__file__).resolve().parents[1] / "openapi" / "openapi.yaml"
    expected = source.read_bytes()
    if args.check:
        if not target.exists() or target.read_bytes() != expected:
            parser.exit(1, "OpenAPI copy differs; run sync_openapi.py without --check.\n")
        print("OpenAPI copies match.")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(expected)
        print("Updated openapi/openapi.yaml from Nodus/design/openapi.yaml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
