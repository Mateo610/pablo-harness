"""Rosters and scores from CSV text, and the grade export."""

import csv
import io

from gradebook.book import Gradebook
from gradebook.compute import final_percent, letter
from gradebook.models import Score, Student
from gradebook.policy import Policy


def load_roster(text: str, book: Gradebook) -> None:
    """Columns: id,name"""
    for row in csv.DictReader(io.StringIO(text)):
        book.add_student(Student(row["id"].strip(), row["name"].strip()))


def load_scores(text: str, book: Gradebook) -> None:
    """Columns: student,assignment,points[,late_days]"""
    for row in csv.DictReader(io.StringIO(text)):
        book.record(
            Score(
                student_id=row["student"].strip(),
                assignment_id=row["assignment"].strip(),
                points=float(row["points"]),
                late_days=int(row.get("late_days") or 0),
            )
        )


def export_grades(book: Gradebook, policy: Policy) -> str:
    """One row per student, sorted by id: id,name,percent,letter"""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["id", "name", "percent", "letter"])
    for sid in sorted(book.students):
        pct = final_percent(book, sid, policy)
        writer.writerow([sid, book.students[sid].name, f"{pct:.1f}", letter(pct)])
    return out.getvalue()
