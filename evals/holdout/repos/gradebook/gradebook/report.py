"""Readable reports for one student or the whole class."""

from gradebook.book import Gradebook
from gradebook.compute import category_average, final_percent, letter
from gradebook.policy import Policy


def student_report(book: Gradebook, student_id: str, policy: Policy) -> str:
    student = book.students[student_id]
    lines = [f"{student.name} ({student.id})"]
    for category in policy.weights:
        avg = category_average(book, student_id, category, policy)
        shown = "-" if avg is None else f"{avg * 100:.1f}%"
        lines.append(f"  {category}: {shown}")
    pct = final_percent(book, student_id, policy)
    lines.append(f"  final: {pct:.1f}% {letter(pct)}")
    return "\n".join(lines)


def class_summary(book: Gradebook, policy: Policy) -> dict[str, object]:
    percents = sorted(final_percent(book, sid, policy) for sid in book.students)
    letters: dict[str, int] = {}
    for p in percents:
        letters[letter(p)] = letters.get(letter(p), 0) + 1
    return {
        "students": len(percents),
        "mean": sum(percents) / len(percents) if percents else 0.0,
        "median": percents[len(percents) // 2] if percents else 0.0,
        "letters": letters,
    }
