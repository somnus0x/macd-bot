import os

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from bot import compute_ema_trend, compute_macd, compute_rsi, composite_signal, ema


def flat_klines(count, volume=100.0):
    return [[0, 0, 0, 0, 0, volume] for _ in range(count)]


def test_ema_known_input_known_output():
    assert ema([1, 2, 3, 4, 5], 3) == [2.0, 3.0, 4.0]


def test_ema_returns_empty_when_values_shorter_than_period():
    assert ema([10.0, 11.0], 3) == []


def test_compute_macd_known_rising_series_output():
    closes = [100 + i * 0.5 for i in range(40)]

    assert compute_macd(closes) == {
        "macd": 3.5,
        "signal": 3.5,
        "diff": -0.0,
        "histogram": -0.0,
        "cross": None,
    }


def test_compute_macd_known_falling_series_output():
    closes = [
        100, 101, 102, 101, 100, 99, 98, 97, 96, 95,
        94, 93, 92, 91, 90, 89, 88, 87, 86, 85,
        84, 83, 82, 81, 80, 79, 78, 77, 76, 75,
        74, 73, 72, 71, 70, 69, 68, 67, 66, 65,
    ]

    assert compute_macd(closes) == {
        "macd": -6.9261,
        "signal": -6.8991,
        "diff": -0.0269,
        "histogram": -0.0269,
        "cross": None,
    }


def test_compute_macd_returns_none_for_insufficient_closes():
    assert compute_macd([float(i) for i in range(34)]) is None


def test_compute_rsi_all_up_closes_is_near_100():
    rsi = compute_rsi([float(i) for i in range(1, 17)])

    assert rsi == pytest.approx(100.0)


def test_compute_rsi_all_down_closes_is_near_0():
    rsi = compute_rsi([float(i) for i in range(16, 0, -1)])

    assert rsi == pytest.approx(0.0)


def test_compute_rsi_returns_none_for_insufficient_closes():
    assert compute_rsi([1.0] * 14) is None


def test_compute_ema_trend_shape_and_values_for_rising_closes():
    trend = compute_ema_trend([float(i) for i in range(1, 61)])

    assert set(trend) == {
        "ema20",
        "ema50",
        "above_ema20",
        "above_ema50",
        "ema20_above_ema50",
    }
    assert trend["ema20"] == pytest.approx(50.5)
    assert trend["ema50"] == pytest.approx(35.5)
    assert trend["above_ema20"] is True
    assert trend["above_ema50"] is True
    assert trend["ema20_above_ema50"] is True


def test_compute_ema_trend_returns_none_for_insufficient_closes():
    assert compute_ema_trend([float(i) for i in range(49)]) is None


def test_composite_signal_shape_and_nested_indicator_outputs():
    closes = [float(i) for i in range(1, 61)]
    result = composite_signal(closes, flat_klines(60))

    assert set(result) == {
        "score",
        "verdict",
        "signals",
        "macd",
        "rsi",
        "ema",
        "volume",
    }
    assert result["score"] == -10
    assert result["verdict"] == "NEUTRAL"
    assert "RSI overbought (100.0)" in result["signals"]
    assert result["macd"]["macd"] == pytest.approx(7.0)
    assert result["rsi"] == pytest.approx(100.0)
    assert result["ema"]["ema20"] == pytest.approx(50.5)
    assert result["volume"]["ratio"] == pytest.approx(1.0)
