import asyncio

import pytest
import requests

from app.modules.whatsapp import media
from app.modules.whatsapp.media import MediaTooLargeError, TwilioMediaFetcher
from app.shared.exceptions import IntegrationError, InvalidRequestError

URL = "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1"


class FakeResponse:
    def __init__(self, chunks: list[bytes], status: int = 200) -> None:
        self.chunks = chunks
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise requests.HTTPError(f"HTTP {self.status}")

    def iter_content(self, size: int):
        yield from self.chunks


class Calls(list):
    """Reemplaza requests.get y registra cada llamada."""

    def __init__(self, monkeypatch) -> None:
        super().__init__()
        self._monkeypatch = monkeypatch

    def install(self, response: FakeResponse) -> None:
        def fake_get(url, **kwargs):
            self.append({"url": url, **kwargs})
            return response

        self._monkeypatch.setattr(media.requests, "get", fake_get)


@pytest.fixture
def calls(monkeypatch) -> Calls:
    return Calls(monkeypatch)


def fetch(fetcher: TwilioMediaFetcher, url: str = URL, max_bytes: int = 1000) -> bytes:
    return asyncio.run(fetcher.fetch(url, max_bytes))


def test_downloads_with_twilio_credentials(calls) -> None:
    calls.install(FakeResponse([b"%PDF-", b"1.4"]))

    content = fetch(TwilioMediaFetcher("AC123", "token"))

    assert content == b"%PDF-1.4"
    assert calls[0]["auth"] == ("AC123", "token")
    assert calls[0]["stream"] is True


def test_rejects_non_twilio_urls() -> None:
    with pytest.raises(InvalidRequestError):
        fetch(TwilioMediaFetcher("AC123", "token"), url="https://evil.example.com/file.pdf")


def test_requires_credentials() -> None:
    with pytest.raises(IntegrationError):
        fetch(TwilioMediaFetcher("", "token"))


def test_stops_when_the_file_is_too_large(calls) -> None:
    calls.install(FakeResponse([b"x" * 600, b"x" * 600]))

    with pytest.raises(MediaTooLargeError):
        fetch(TwilioMediaFetcher("AC123", "token"), max_bytes=1000)


def test_http_errors_become_integration_errors(calls) -> None:
    calls.install(FakeResponse([], status=401))

    with pytest.raises(IntegrationError):
        fetch(TwilioMediaFetcher("AC123", "token"))


def test_repr_hides_credentials() -> None:
    assert "token" not in repr(TwilioMediaFetcher("AC123", "token"))
