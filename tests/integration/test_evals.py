from evals.run import load_tasks, run_suite


async def test_offline_evals_cover_ten_isolated_tasks() -> None:
    assert len(load_tasks()) >= 10
    report = await run_suite("fake", "fixture")
    assert report["kind"] == "harness_protocol"
    assert report["passed"] == report["total"] == 11
    resumed = next(item for item in report["results"] if item["id"] == "resume-after-failure")
    assert resumed["run_statuses"] == ["failed", "completed"]


async def test_eval_grader_reports_a_real_failure() -> None:
    task = next(item for item in load_tasks() if item["id"] == "read-config")
    task["checks"] = [{"kind": "answer_contains", "value": "WRONG-MARKER"}]
    from evals.run import run_task

    result = await run_task(task, "fake", "fixture")
    assert result["passed"] is False
    assert result["failures"] == ["answer_contains"]
