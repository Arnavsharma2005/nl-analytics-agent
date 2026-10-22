"""Train the learned router (app/agents/learned_router.py) from semantic/router.yml and the dev eval questions.

Usage (from the repo root; no keys or database needed):
    python eval/train_router.py            # train, report on the held-out fifth, write apps/api/semantic/router.json
    python eval/train_router.py --check    # train and report only; exit 1 if the model is not good enough

The held-out fifth is chosen by a hash of each question, so it stays the same as examples are added. The model is
written only when it is wrong on at most 2% of the held-out questions it is sure about. Turn it on with
LEARNED_ROUTER=true.
"""

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.agents.learned_router import THRESHOLD, LearnedRouter, evaluate, examples, held_out  # noqa: E402

SEMANTIC = ROOT / "apps" / "api" / "semantic"


def labelled() -> list[tuple[str, str]]:
    """The router.yml examples plus the dev split of the eval set, which is all data_query. The test split stays out:
    it is what the whole pipeline is scored on."""
    dev = [(q["question"].strip(), "data_query") for q in yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text())
           if q["split"] == "dev"]
    seen = {q.lower() for q, _ in examples(SEMANTIC / "router.yml")}
    return examples(SEMANTIC / "router.yml") + [(q, label) for q, label in dev if q.lower() not in seen]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report only; don't write the model")
    parser.add_argument("--out", type=Path, default=SEMANTIC / "router.json", help="where to write the model")
    args = parser.parse_args(argv)

    data = labelled()
    train = [e for e in data if not held_out(e[0])]
    test = [e for e in data if held_out(e[0])]
    report = evaluate(LearnedRouter.train(train), test, len(data))
    print(f"{report.examples} examples, {report.held_out} held out. Held-out accuracy {report.accuracy:.1%}; "
          f"sure (p >= {THRESHOLD}) about {report.coverage:.1%} of them, and wrong on {report.confident_error:.1%} "
          f"of those.")
    if not report.good_enough:
        print("Not good enough to use: add examples where it goes wrong, then train again.")
        return 1
    if not args.check:
        LearnedRouter.train(data).save(args.out)  # the model in use learns from every example
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
