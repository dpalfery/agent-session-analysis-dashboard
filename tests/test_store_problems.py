import os, sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentdash.canonical import Problem, Session
from agentdash.store import Store


def _temporary_store():
    directory = tempfile.mkdtemp()
    return directory, os.path.join(directory, "test.db"), Store(
        os.path.join(directory, "test.db")
    )


def test_problem_table_has_harness_column():
    directory, _path, store = _temporary_store()
    try:
        columns = {
            row["name"]
            for row in store.db.execute("PRAGMA table_info(problem)")
        }

        assert "harness" in columns
    finally:
        store.close()
        shutil.rmtree(directory)


def test_problems_do_not_accumulate_across_rebuilds():
    directory, _path, store = _temporary_store()
    try:
        session = Session(
            session_id="s1",
            harness="gemini",
            label="x",
            span_ids=[],
            requests=[],
        )
        problem = Problem(
            severity="error",
            code="x",
            message="m",
            span_id="abc",
            session_id=None,
        )

        store.replace_sessions("gemini", [(session, {})], problems=[problem])
        count_after_first = store.db.execute(
            "SELECT COUNT(*) FROM problem"
        ).fetchone()[0]
        assert count_after_first == 1

        store.replace_sessions("gemini", [(session, {})], problems=[problem])
        count_after_second = store.db.execute(
            "SELECT COUNT(*) FROM problem"
        ).fetchone()[0]
        assert count_after_second == 1

        row = store.db.execute(
            "SELECT harness FROM problem"
        ).fetchone()
        assert row["harness"] == "gemini"
    finally:
        store.close()
        shutil.rmtree(directory)


if __name__ == "__main__":
    test_problem_table_has_harness_column()
    test_problems_do_not_accumulate_across_rebuilds()
    print("ALL STORE PROBLEM TESTS PASSED SUCCESSFULLY!")
