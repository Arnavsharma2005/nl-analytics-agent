"""Show or change who gets each feature (app/flags.py). The app picks a change up within a minute, without a deploy.

Usage (with APP_DATABASE_URL, or DATABASE_URL, set as on the server):
    python scripts/flags.py                           # every flag and who gets it
    python scripts/flags.py set deep_analysis users   # off, curators, users or everyone
    python scripts/flags.py reset deep_analysis       # follow the DEEP_ANALYSIS setting again
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.flags import AUDIENCES, FLAGS  # noqa: E402
from app.main import get_flags  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command")
    set_ = commands.add_parser("set", help="set who gets a flag")
    set_.add_argument("name", choices=list(FLAGS))
    set_.add_argument("audience", choices=list(AUDIENCES))
    reset = commands.add_parser("reset", help="make a flag follow its setting again")
    reset.add_argument("name", choices=list(FLAGS))
    args = parser.parse_args(argv)

    flags = get_flags()
    if args.command == "set":
        flags.set(args.name, args.audience)
    elif args.command == "reset":
        flags.set(args.name, None)
    width = max(map(len, FLAGS))
    for name, audience in flags.audiences().items():
        print(f"{name:<{width}}  {audience:<8}  {FLAGS[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
