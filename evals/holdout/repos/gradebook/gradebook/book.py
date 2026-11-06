"""The gradebook itself: who is enrolled, what was assigned, what they scored."""

from gradebook.models import Assignment, Score, Student


class Gradebook:
    def __init__(self) -> None:
        self.students: dict[str, Student] = {}
        self.assignments: dict[str, Assignment] = {}
        self.scores: list[Score] = []

    def add_student(self, student: Student) -> None:
        self.students[student.id] = student

    def add_assignment(self, assignment: Assignment) -> None:
        self.assignments[assignment.id] = assignment

    def record(self, score: Score) -> None:
        if score.student_id not in self.students:
            raise KeyError(f"unknown student {score.student_id}")
        if score.assignment_id not in self.assignments:
            raise KeyError(f"unknown assignment {score.assignment_id}")
        self.scores = [
            s
            for s in self.scores
            if (s.student_id, s.assignment_id) != (score.student_id, score.assignment_id)
        ]
        self.scores.append(score)

    def scores_for(self, student_id: str) -> list[Score]:
        return [s for s in self.scores if s.student_id == student_id]

    def in_category(self, category: str) -> list[Assignment]:
        return [a for a in self.assignments.values() if a.category == category]
