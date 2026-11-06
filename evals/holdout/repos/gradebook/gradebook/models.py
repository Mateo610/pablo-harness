"""The records a gradebook keeps."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Student:
    id: str
    name: str


@dataclass(frozen=True)
class Assignment:
    id: str
    title: str
    category: str  # homework, quiz, exam, ...
    max_points: float
    due: str  # YYYY-MM-DD


@dataclass(frozen=True)
class Score:
    student_id: str
    assignment_id: str
    points: float
    late_days: int = 0
