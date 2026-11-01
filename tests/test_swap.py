from __future__ import annotations

from pablo.swap import SwapView, loaded_ids, parse_running, swap_times


def test_parse_running_list_of_names() -> None:
    assert parse_running({"running": ["coder", "router"]}) == {"coder": "ready", "router": "ready"}
    assert loaded_ids({"running": []}) == []


def test_parse_running_process_objects() -> None:
    payload = {
        "running": [
            {"model": "coder", "state": "ready"},
            {"model": "judge", "state": "starting"},
            {"model": "router", "state": "stopped"},
        ]
    }
    assert parse_running(payload)["judge"] == "starting"
    assert loaded_ids(payload) == ["coder"]


def test_parse_running_single_object_and_map() -> None:
    assert parse_running({"model": "coder", "state": "ready"}) == {"coder": "ready"}
    assert parse_running({"coder": {"state": "ready"}, "router": "ready"}) == {
        "coder": "ready",
        "router": "ready",
    }


def test_swap_view_absent_and_unknown() -> None:
    view = SwapView(lambda: {"running": ["router"]})
    assert view.state("router") == "ready"
    assert view.state("judge") == "absent"

    def down() -> dict[str, str]:
        raise OSError("down")

    assert SwapView(down).state("judge") == "unknown"


def test_swap_times_both_directions() -> None:
    samples = [
        (0.0, {"coder": "ready", "router": "ready"}),
        (1.0, {"coder": "stopping", "router": "ready"}),
        (4.0, {"judge": "ready", "router": "ready"}),
        (9.0, {"judge": "ready", "router": "ready"}),
        (10.0, {"judge": "stopping", "router": "ready"}),
        (12.5, {"coder": "ready", "router": "ready"}),
    ]
    assert swap_times(samples) == ([3.0], [2.5])


def test_swap_times_direct_jump_uses_previous_sample() -> None:
    samples = [
        (0.0, {"coder": "ready"}),
        (0.4, {"judge": "ready"}),
    ]
    assert swap_times(samples) == ([0.4], [])


def test_parse_running_rejects_junk() -> None:
    assert parse_running(None) == {}
    assert parse_running(["coder"]) == {}
    assert loaded_ids(["coder"]) == []
