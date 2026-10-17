"""The batch planner: many queued edits become one desired chapter list."""
import pytest

from app.tonies_batch import ATTACH_MARK, apply_ops, renamed_title


def ch(identity, seconds=10, title=None):
    return {"id": identity, "title": title or identity.upper(), "file": f"f-{identity}", "seconds": seconds,
            "type": "file", "transcoding": False}


def plan(chapters, ops, capacity=10_000, max_chapters=99):
    desired, errors = apply_ops(chapters, ops, capacity_seconds=capacity, max_chapters=max_chapters)
    return [c["id"] for c in desired], errors, desired


def test_ops_apply_in_order_against_the_running_result():
    ids, errors, _ = plan([ch("a"), ch("b"), ch("c"), ch("d")], [
        {"type": "delete", "chapter_id": "b"},
        {"type": "move", "chapter_id": "d", "position": {"edge": "start"}},
        {"type": "delete", "chapter_id": "a"},
        {"type": "move", "chapter_id": "c", "position": {"before": "d"}},
    ])
    assert ids == ["c", "d"]
    assert errors == [None, None, None, None]


def test_a_failing_op_is_skipped_and_the_rest_still_apply():
    ids, errors, _ = plan([ch("a"), ch("b")], [
        {"type": "delete", "chapter_id": "gone"},
        {"type": "delete", "chapter_id": "a"},
        {"type": "delete", "chapter_id": "a"},
    ])
    assert ids == ["b"]
    assert errors[0] and errors[2] and errors[1] is None
    assert "no longer on this Tonie" in errors[0]


def test_rename_keeps_the_hidden_track_token():
    _, errors, desired = plan([ch("a", title="Old [oc:ab12cd]")], [{"type": "rename", "chapter_id": "a", "title": "New"}])
    assert errors == [None] and desired[0]["title"] == "New [oc:ab12cd]"
    assert renamed_title("x", "  Plain  ") == "Plain"


@pytest.mark.parametrize("position,expected", [
    ({"edge": "start"}, ["n", "a", "b", "c"]),
    ({"edge": "end"}, ["a", "b", "c", "n"]),
    ({"before": "b"}, ["a", "n", "b", "c"]),
    ({"after": "b"}, ["a", "b", "n", "c"]),
    ({"index": 1}, ["a", "n", "b", "c"]),
    ({"index": 99}, ["a", "b", "c", "n"]),
    (None, ["a", "b", "c", "n"]),
])
def test_attach_positions(position, expected):
    ids, errors, desired = plan([ch("a"), ch("b"), ch("c")],
                                [{"type": "attach", "file_id": "n", "title": "New", "seconds": 5, "position": position}])
    assert ids == expected and errors == [None]
    assert desired[expected.index("n")][ATTACH_MARK] == 0


def test_an_anchor_deleted_earlier_in_the_batch_falls_back_to_its_index():
    ids, errors, _ = plan([ch("a"), ch("b"), ch("c")], [
        {"type": "delete", "chapter_id": "b"},
        {"type": "attach", "file_id": "n", "title": "New", "seconds": 5, "position": {"before": "b", "index": 1}},
        {"type": "move", "chapter_id": "c", "position": {"before": "b"}},
    ])
    assert ids == ["a", "n", "c"]
    assert errors[:2] == [None, None] and "no longer exists" in errors[2]


def test_capacity_and_chapter_limit_count_the_whole_batch():
    ids, errors, _ = plan([ch("a", 50)], [
        {"type": "attach", "file_id": "n1", "title": "One", "seconds": 30},
        {"type": "attach", "file_id": "n2", "title": "Two", "seconds": 30},
        {"type": "delete", "chapter_id": "a"},
        {"type": "attach", "file_id": "n3", "title": "Three", "seconds": 30},
    ], capacity=100)
    assert ids == ["n1", "n3"]
    assert errors[1] == "Not enough free Tonies capacity"
    _, errors, _ = plan([ch("a")], [{"type": "attach", "file_id": "n", "title": "N", "seconds": 1}], max_chapters=1)
    assert errors == ["Tonies chapter limit reached"]


def test_attach_refuses_reused_file_ids_and_duplicate_titles():
    _, errors, _ = plan([ch("a")], [
        {"type": "attach", "file_id": "a", "title": "X", "seconds": 1},
        {"type": "attach", "file_id": "n1", "title": "Same", "seconds": 1},
        {"type": "attach", "file_id": "n2", "title": "Same", "seconds": 1},
    ])
    assert errors[0] and errors[1] is None and errors[2]


def test_clear_then_attach():
    ids, errors, _ = plan([ch("a"), ch("b")], [{"type": "clear"},
                                               {"type": "attach", "file_id": "n", "title": "N", "seconds": 1}])
    assert ids == ["n"] and errors == [None, None]


def test_legacy_index_targets_and_expected_positions():
    ids, errors, _ = plan([ch("a"), ch("b")], [
        {"type": "move", "chapter_id": "b", "expect_index": 0, "position": {"index": 0}},
        {"type": "move", "index": 1, "expect_index": 1, "position": {"index": 0}},
        {"type": "move", "chapter_id": "a", "position": {"index": 5}},
    ])
    assert ids == ["b", "a"]
    assert "stale" in errors[0] and errors[1] is None and "Invalid track position" in errors[2]


def test_input_list_is_not_mutated():
    chapters = [ch("a"), ch("b")]
    plan(chapters, [{"type": "delete", "chapter_id": "a"}, {"type": "rename", "chapter_id": "b", "title": "Z"}])
    assert [c["id"] for c in chapters] == ["a", "b"] and chapters[1]["title"] == "B"
