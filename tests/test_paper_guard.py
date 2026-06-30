"""Paper-mode safety-gate tests — these enforce the #1 hard constraint."""
import pytest

from config import Secrets
from src.broker import assert_paper_mode, LiveModeError


def _secrets(url: str, key: str = "PKTEST", secret: str = "SEC") -> Secrets:
    return Secrets(alpaca_api_key=key, alpaca_secret_key=secret,
                   openai_api_key="oai", alpaca_base_url=url)


def test_paper_url_passes():
    # Should not raise.
    assert_paper_mode(_secrets("https://paper-api.alpaca.markets"))


def test_live_url_rejected():
    with pytest.raises(LiveModeError):
        assert_paper_mode(_secrets("https://api.alpaca.markets"))


def test_unknown_url_rejected():
    with pytest.raises(LiveModeError):
        assert_paper_mode(_secrets("https://example.com"))


def test_missing_keys_rejected():
    with pytest.raises(LiveModeError):
        assert_paper_mode(_secrets("https://paper-api.alpaca.markets", key="", secret=""))
