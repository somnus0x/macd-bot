import os

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from bot import (
    compute_bollinger_bands,
    compute_ema_trend,
    compute_macd,
    compute_rsi,
    compute_stochastic,
    composite_signal,
    ema,
)


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


def test_compute_bollinger_bands_known_input_known_output():
    bands = compute_bollinger_bands([float(i) for i in range(1, 21)])

    assert bands == {
        "upper": 22.0326,
        "middle": 10.5,
        "lower": -1.0326,
        "width_pct": 219.67,
        "percent_b": 0.9119,
        "zone": "NEAR_UPPER",
    }


def test_compute_bollinger_bands_flat_closes_returns_middle_position():
    bands = compute_bollinger_bands([10.0] * 20)

    assert bands == {
        "upper": 10.0,
        "middle": 10.0,
        "lower": 10.0,
        "width_pct": 0.0,
        "percent_b": 0.5,
        "zone": "MIDDLE",
    }


def test_compute_bollinger_bands_detects_price_below_lower_band():
    bands = compute_bollinger_bands([10.0] * 19 + [0.0])

    assert bands == {
        "upper": 13.8589,
        "middle": 9.5,
        "lower": 5.1411,
        "width_pct": 91.77,
        "percent_b": -0.5897,
        "zone": "BELOW_LOWER",
    }


def test_compute_bollinger_bands_returns_none_for_insufficient_closes():
    assert compute_bollinger_bands([1.0] * 19) is None


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


def test_compute_stochastic_known_textbook_values():
    highs = [10, 12, 14, 13, 15]
    lows = [8, 9, 10, 9, 11]
    closes = [9, 11, 13, 12, 14]

    result = compute_stochastic(highs, lows, closes, k_period=3, d_period=2)

    # %K(idx 2-4): 100*(14-9)/(15-9) = 83.3333 -> 83.33
    # %K(idx 1-3): 100*(12-9)/(14-9) = 60.0
    # %D = (83.3333 + 60.0) / 2 = 71.6667 -> 71.67
    assert result == {"k": 83.33, "d": 71.67}


def test_compute_stochastic_returns_none_for_insufficient_candles():
    highs = [float(i) + 2 for i in range(13)]
    lows = [float(i) for i in range(13)]
    closes = [float(i) + 1 for i in range(13)]

    assert compute_stochastic(highs, lows, closes) is None


def test_compute_stochastic_d_period_boundary():
    highs = [float(i) + 2 for i in range(16)]
    lows = [float(i) for i in range(16)]
    closes = [float(i) + 1 for i in range(16)]

    assert compute_stochastic(highs[:15], lows[:15], closes[:15]) is None

    result = compute_stochastic(highs, lows, closes)
    assert result is not None
    assert isinstance(result["k"], (int, float))
    assert isinstance(result["d"], (int, float))


def test_compute_stochastic_flat_range_is_neutral():
    highs = [5.0] * 16
    lows = [5.0] * 16
    closes = [5.0] * 16

    result = compute_stochastic(highs, lows, closes)

    assert result == {"k": 50.0, "d": 50.0}


def test_compute_stochastic_honors_custom_periods():
    highs = [float(i) + 3 for i in range(20)]
    lows = [float(i) for i in range(20)]
    closes = [float(i) + 1.5 for i in range(20)]

    default_result = compute_stochastic(highs, lows, closes)
    custom_result = compute_stochastic(highs, lows, closes, k_period=5, d_period=2)

    assert default_result is not None
    assert custom_result is not None
    assert default_result != custom_result


def test_compute_stochastic_rounds_to_two_decimals():
    highs = [10, 12, 14, 13, 15]
    lows = [8, 9, 10, 9, 11]
    closes = [9, 11, 13, 12, 14]

    result = compute_stochastic(highs, lows, closes, k_period=3, d_period=2)

    assert round(result["k"], 2) == result["k"]
    assert round(result["d"], 2) == result["d"]


def test_compute_stochastic_result_is_all_or_nothing():
    highs = [float(i) + 2 for i in range(16)]
    lows = [float(i) for i in range(16)]
    closes = [float(i) + 1 for i in range(16)]

    result = compute_stochastic(highs, lows, closes)

    assert result is not None
    assert result["k"] is not None
    assert result["d"] is not None
    assert isinstance(result["k"], (int, float))
    assert isinstance(result["d"], (int, float))


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
        "bollinger",
        "volume",
        "stochastic",
    }
    assert result["score"] == -10
    assert result["verdict"] == "NEUTRAL"
    assert "RSI overbought (100.0)" in result["signals"]
    assert result["macd"]["macd"] == pytest.approx(7.0)
    assert result["rsi"] == pytest.approx(100.0)
    assert result["ema"]["ema20"] == pytest.approx(50.5)
    assert result["bollinger"]["zone"] == "NEAR_UPPER"
    assert result["volume"]["ratio"] == pytest.approx(1.0)
    assert result["stochastic"] == {"k": 50.0, "d": 50.0}


def ohlc_klines(count, high, low, close, volume=100.0):
    return [[0, 0, high, low, close, volume] for _ in range(count)]


def _stochastic_signals(result):
    return [s for s in result["signals"] if "Stochastic" in s]


def test_composite_appends_stochastic_overbought_context():
    closes = [90.0] * 60
    result = composite_signal(closes, ohlc_klines(60, 100.0, 0.0, 90.0))

    assert result["stochastic"]["k"] == 90.0
    stoch = _stochastic_signals(result)
    assert len(stoch) == 1
    assert "overbought" in stoch[0].lower()
    assert all("BB" not in s for s in stoch)


def test_composite_appends_stochastic_oversold_context():
    closes = [10.0] * 60
    result = composite_signal(closes, ohlc_klines(60, 100.0, 0.0, 10.0))

    assert result["stochastic"]["k"] == 10.0
    stoch = _stochastic_signals(result)
    assert len(stoch) == 1
    assert "oversold" in stoch[0].lower()
    assert all("BB" not in s for s in stoch)


def test_composite_appends_stochastic_midrange_context():
    closes = [50.0] * 60
    result = composite_signal(closes, ohlc_klines(60, 100.0, 0.0, 50.0))

    assert result["stochastic"]["k"] == 50.0
    stoch = _stochastic_signals(result)
    assert len(stoch) == 1
    assert "overbought" not in stoch[0].lower()
    assert "oversold" not in stoch[0].lower()


def test_composite_stochastic_strict_overbought_threshold_is_midrange():
    closes = [80.0] * 60
    result = composite_signal(closes, ohlc_klines(60, 100.0, 0.0, 80.0))

    assert result["stochastic"]["k"] == 80.0
    stoch = _stochastic_signals(result)
    assert len(stoch) == 1
    assert "overbought" not in stoch[0].lower()


def test_composite_stochastic_strict_oversold_threshold_is_midrange():
    closes = [20.0] * 60
    result = composite_signal(closes, ohlc_klines(60, 100.0, 0.0, 20.0))

    assert result["stochastic"]["k"] == 20.0
    stoch = _stochastic_signals(result)
    assert len(stoch) == 1
    assert "oversold" not in stoch[0].lower()


def test_composite_stochastic_none_when_klines_too_short():
    closes = [float(i) for i in range(1, 11)]
    result = composite_signal(closes, ohlc_klines(10, 100.0, 0.0, 5.0))

    assert result["stochastic"] is None
    assert _stochastic_signals(result) == []


def test_composite_stochastic_is_context_only_score_unchanged():
    closes = [float(i) for i in range(1, 61)]
    result = composite_signal(closes, flat_klines(60))

    assert result["score"] == -10
    assert result["verdict"] == "NEUTRAL"
