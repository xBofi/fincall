from building_call.repos.operations import summarize_operations


def test_summary_keeps_missing_latency_as_none() -> None:
    summary = summarize_operations([])
    assert summary["calls"] == 0
    assert summary["response_ms"]["p50"] is None
    assert summary["resolved"] == 0
    assert summary["unresolved"] == 0


def test_summary_counts_resolution_and_latency_from_durable_rows() -> None:
    summary = summarize_operations(
        [
            {"result": "incident_created", "response_ms": 600},
            {"result": "unclassifiable", "response_ms": 1400},
        ]
    )
    assert summary["calls"] == 2
    assert summary["response_ms"]["p50"] == 1000
    assert summary["resolved"] == 1
    assert summary["unresolved"] == 1
