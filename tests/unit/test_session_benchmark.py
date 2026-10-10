from scripts.benchmark_session import benchmark


async def test_session_benchmark_reports_all_operations_without_model_calls():
    report = await benchmark([4, 10], 3)
    assert report["repeats"] == 3
    assert report["sessions_per_sample"] == 1
    assert [row["initial_messages"] for row in report["results"]] == [4, 10]
    for row in report["results"]:
        assert set(row["median_ms"]) == {"append", "snapshot", "metadata", "list"}
        assert all(len(samples) == 3 for samples in row["samples_ms"].values())
