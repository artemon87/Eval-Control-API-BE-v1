from app.repositories.unit import (
    DEFAULT_BEHAVIORAL_THRESHOLD,
    DEFAULT_METRIC_THRESHOLD,
    _case_trend_values,
)


def test_metric_trend_uses_recorded_thresholds() -> None:
    score, threshold = _case_trend_values(
        [
            {
                "scores": {"correctness": 4.5},
                "thresholds": {"correctness": 4.0},
            },
            {
                "scores": {"correctness": 5.0},
                "thresholds": {"correctness": 4.2},
            },
        ],
        {"unit_config": {}},
        metric="correctness",
    )

    assert score == 4.75
    assert threshold == 4.1


def test_behavioral_trend_prefers_configured_threshold() -> None:
    score, threshold = _case_trend_values(
        [{"pass_rate": 90, "verdict": "passed"}],
        {
            "unit_config": {
                "evaluation_layer": "behavioral_contracts",
                "consistency_threshold": 85,
            }
        },
    )

    assert score == 0.9
    assert threshold == 0.85


def test_behavioral_trend_uses_recorded_threshold_when_not_configured() -> None:
    _, threshold = _case_trend_values(
        [
            {"pass_rate": 1.0, "thresholds": {"consistency": 0.7}},
            {"pass_rate": 0.5, "thresholds": {"consistency": 0.9}},
        ],
        {"unit_config": {"evaluation_layer": "behavioral-contracts"}},
    )

    assert threshold == 0.8


def test_trend_threshold_fallbacks_are_explicit() -> None:
    _, metric_threshold = _case_trend_values([{"verdict": "failed"}], {})
    _, behavioral_threshold = _case_trend_values(
        [{"verdict": "failed"}],
        {"unit_config": {"evaluation_layer": "behavioral-contracts"}},
    )

    assert metric_threshold == DEFAULT_METRIC_THRESHOLD
    assert behavioral_threshold == DEFAULT_BEHAVIORAL_THRESHOLD
