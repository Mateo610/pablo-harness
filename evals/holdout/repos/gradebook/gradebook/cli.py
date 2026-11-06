"""gradebook report|summary --roster R.csv --scores S.csv --policy P.json [--student ID]"""

import argparse
import json
import sys
from pathlib import Path

from gradebook.book import Gradebook
from gradebook.csvio import load_roster, load_scores
from gradebook.models import Assignment
from gradebook.policy import load_policy
from gradebook.report import class_summary, student_report


def load(args: argparse.Namespace) -> tuple[Gradebook, object]:
    data = json.loads(Path(args.policy).read_text())
    book = Gradebook()
    for a in data.get("assignments", []):
        book.add_assignment(Assignment(a["id"], a["title"], a["category"], float(a["max_points"]), a["due"]))
    load_roster(Path(args.roster).read_text(), book)
    load_scores(Path(args.scores).read_text(), book)
    return book, load_policy(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gradebook")
    parser.add_argument("command", choices=["report", "summary"])
    parser.add_argument("--roster", required=True)
    parser.add_argument("--scores", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--student")
    args = parser.parse_args(argv)
    book, policy = load(args)
    if args.command == "report":
        ids = [args.student] if args.student else sorted(book.students)
        print("\n\n".join(student_report(book, sid, policy) for sid in ids))  # type: ignore[arg-type]
    else:
        print(json.dumps(class_summary(book, policy), sort_keys=True))  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
