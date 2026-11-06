from gradebook.book import Gradebook
from gradebook.compute import final_percent, letter
from gradebook.csvio import export_grades, load_roster, load_scores
from gradebook.models import Assignment
from gradebook.policy import load_policy


def book() -> Gradebook:
    b = Gradebook()
    b.add_assignment(Assignment("hw1", "Homework 1", "homework", 10, "2026-09-10"))
    b.add_assignment(Assignment("ex1", "Midterm", "exam", 100, "2026-10-15"))
    load_roster("id,name\ns1,Ana\ns2,Ben\n", b)
    load_scores("student,assignment,points\ns1,hw1,10\ns1,ex1,80\ns2,hw1,5\ns2,ex1,90\n", b)
    return b


POLICY = load_policy({"weights": {"homework": 0.4, "exam": 0.6}})


def test_final_percent() -> None:
    assert round(final_percent(book(), "s1", POLICY), 1) == 88.0
    assert round(final_percent(book(), "s2", POLICY), 1) == 74.0


def test_letters() -> None:
    assert [letter(p) for p in (95, 85, 75, 65, 10)] == ["A", "B", "C", "D", "F"]


def test_export() -> None:
    assert export_grades(book(), POLICY).splitlines() == [
        "id,name,percent,letter",
        "s1,Ana,88.0,B",
        "s2,Ben,74.0,C",
    ]
