"""Turning scores into percentages and letters."""

from gradebook.book import Gradebook
from gradebook.models import Score
from gradebook.policy import Policy


def earned(score: Score, max_points: float, policy: Policy) -> float:
    """Points after the late penalty, never below zero."""
    penalty = score.points * policy.late_penalty * score.late_days
    return max(0.0, score.points - penalty) / max_points


def category_average(book: Gradebook, student_id: str, category: str, policy: Policy) -> float | None:
    """Mean fraction (0..1) over the graded assignments in a category; None if none are graded."""
    fractions = []
    for score in book.scores_for(student_id):
        assignment = book.assignments[score.assignment_id]
        if assignment.category == category:
            fractions.append(earned(score, assignment.max_points, policy))
    if not fractions:
        return None
    return sum(fractions) / len(fractions)


def final_percent(book: Gradebook, student_id: str, policy: Policy) -> float:
    total = 0.0
    for category, weight in policy.weights.items():
        avg = category_average(book, student_id, category, policy)
        if avg is not None:
            total += avg * weight
    return total * 100


def letter(percent: float) -> str:
    if percent >= 90:
        return "A"
    if percent >= 80:
        return "B"
    if percent >= 70:
        return "C"
    if percent >= 60:
        return "D"
    return "F"
