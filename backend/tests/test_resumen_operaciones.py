from building_call.repos.operaciones import resumir_operaciones


def test_summary_keeps_missing_latency_as_none() -> None:
    summary = resumir_operaciones([])
    assert summary["llamadas"] == 0
    assert summary["respuesta_ms"]["p50"] is None
    assert summary["resueltas"] == 0
    assert summary["no_resueltas"] == 0


def test_summary_counts_resolution_and_latency_from_durable_rows() -> None:
    summary = resumir_operaciones(
        [
            {"resultado": "incidencia_creada", "respuesta_ms": 600},
            {"resultado": "no_clasificable", "respuesta_ms": 1400},
        ]
    )
    assert summary["llamadas"] == 2
    assert summary["respuesta_ms"]["p50"] == 1000
    assert summary["resueltas"] == 1
    assert summary["no_resueltas"] == 1
