import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from bot import compute_kelly_fraction, kelly_command


class DummyMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append({"text": text, "kwargs": kwargs})


def run_kelly_command(args):
    message = DummyMessage()
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(args=args)

    asyncio.run(kelly_command(update, context))

    return message.replies


def test_compute_kelly_fraction_returns_rounded_full_and_half_kelly():
    assert compute_kelly_fraction(0.55, 2.0) == {
        "win_probability": 0.55,
        "loss_probability": 0.45,
        "reward_risk": 2.0,
        "edge": 0.65,
        "kelly_fraction": 0.325,
        "half_kelly_fraction": 0.1625,
        "has_edge": True,
    }


def test_compute_kelly_fraction_negative_edge_keeps_full_and_floors_half():
    assert compute_kelly_fraction(0.4, 1.0) == {
        "win_probability": 0.4,
        "loss_probability": 0.6,
        "reward_risk": 1.0,
        "edge": -0.2,
        "kelly_fraction": -0.2,
        "half_kelly_fraction": 0.0,
        "has_edge": False,
    }


@pytest.mark.parametrize(
    ("win_probability", "reward_risk"),
    [
        (-0.01, 1.0),
        (1.01, 1.0),
        (0.55, 0.0),
        (0.55, -1.0),
    ],
)
def test_compute_kelly_fraction_rejects_invalid_inputs(win_probability, reward_risk):
    with pytest.raises(ValueError):
        compute_kelly_fraction(win_probability, reward_risk)


@pytest.mark.parametrize("win_rate_arg", ["55", "55%", "0.55"])
def test_kelly_command_accepts_supported_probability_formats(win_rate_arg):
    replies = run_kelly_command([win_rate_arg, "2"])

    assert len(replies) == 1
    assert replies[0]["kwargs"] == {"parse_mode": "Markdown"}
    text = replies[0]["text"]
    assert "Win rate: *55.00%*" in text
    assert "Reward:Risk: *2.00R*" in text
    assert "Full Kelly: *32.50%*" in text
    assert "Half Kelly: *16.25%*" in text


def test_kelly_command_includes_bankroll_half_kelly_position_size():
    replies = run_kelly_command(["55", "2", "1,000"])

    assert len(replies) == 1
    text = replies[0]["text"]
    assert "Bankroll: $1,000.00" in text
    assert "Half-Kelly size: *$162.50*" in text


def test_kelly_command_reports_no_position_when_edge_is_not_positive():
    replies = run_kelly_command(["40", "1"])

    assert len(replies) == 1
    text = replies[0]["text"]
    assert "Full Kelly: *-20.00%*" in text
    assert "Half Kelly: *0.00%*" in text
    assert "No position is suggested by Kelly." in text


def test_kelly_command_replies_with_usage_when_arguments_are_missing():
    replies = run_kelly_command([])

    assert len(replies) == 1
    assert "Usage: `/kelly WIN_RATE REWARD_RISK [BANKROLL]`" in replies[0]["text"]
    assert replies[0]["kwargs"] == {"parse_mode": "Markdown"}


@pytest.mark.parametrize(
    "args",
    [
        ["not-a-number", "2"],
        ["150", "2"],
        ["55", "0"],
        ["55", "-1"],
        ["55", "2", "not-a-bankroll"],
    ],
)
def test_kelly_command_rejects_invalid_numeric_inputs(args):
    replies = run_kelly_command(args)

    assert len(replies) == 1
    assert "Invalid inputs" in replies[0]["text"]
    assert replies[0]["kwargs"] == {"parse_mode": "Markdown"}


def test_kelly_command_rejects_nonpositive_bankroll():
    replies = run_kelly_command(["55", "2", "0"])

    assert len(replies) == 1
    assert "Bankroll must be positive." in replies[0]["text"]
