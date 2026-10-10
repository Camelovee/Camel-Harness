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


async def test_memory_protocol_compares_enabled_and_disabled_without_paid_models():
    from evals.memory_protocol import run_suite as run_memory_suite

    report = await run_memory_suite()
    assert report["kind"] == "memory_context_protocol"
    assert report["provider"] == "fake"
    assert report["passed"] == report["total"] == 6
    for result in report["results"]:
        assert [mode["enabled"] for mode in result["modes"]] == [False, True]
        assert all(mode["wrong_references"] == 0 for mode in result["modes"])
        assert all(mode["missing_complete_conditions"] == 0 for mode in result["modes"])
