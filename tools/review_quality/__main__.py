from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evaluation import evaluate_dataset


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate paired, provider-free code-review quality fixtures."
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with args.dataset.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    report = evaluate_dataset(payload).as_dict()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
