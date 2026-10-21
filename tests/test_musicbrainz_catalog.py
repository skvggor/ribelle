from __future__ import annotations

import json
from collections.abc import Iterator
from email.message import Message
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from ribelle.application.album_catalog import AlbumCatalog
from ribelle.domain.album_metadata import AlbumMetadata, TrackMetadata
from ribelle.infrastructure.musicbrainz_catalog import (
    MusicBrainzCatalog,
    MusicBrainzUnavailableError,
)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *arguments: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def create_catalog() -> MusicBrainzCatalog:
    return MusicBrainzCatalog(sleep=lambda duration: None)


def test_search_returns_release_metadata_with_tracks_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: Iterator[FakeResponse] = iter(
        [
            FakeResponse({"releases": [{"id": "release-id"}]}),
            FakeResponse(
                {
                    "title": "Album",
                    "date": "2024-03-12",
                    "artist-credit": [{"artist": {"name": "Artist"}, "joinphrase": ""}],
                    "media": [
                        {
                            "tracks": [
                                {"position": 2, "title": "Second"},
                                {"position": 1, "title": "First"},
                            ]
                        },
                        {"tracks": [{"position": 1, "title": "Third"}]},
                    ],
                }
            ),
        ]
    )
    requests: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        requests.append(request)
        assert timeout == 15
        return next(responses)

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    albums = create_catalog().search("Artist", "Album")

    assert albums == (
        AlbumMetadata(
            artist="Artist",
            title="Album",
            year="2024",
            tracks=(
                TrackMetadata(number=1, title="First"),
                TrackMetadata(number=2, title="Second"),
                TrackMetadata(number=1, title="Third", disc_number=2),
            ),
        ),
    )
    assert len(requests) == 2
    assert "artist%3A%22Artist%22" in requests[0].full_url
    assert "recordings" in requests[1].full_url
    assert requests[0].get_header("User-agent")


def test_search_returns_no_albums_when_musicbrainz_finds_no_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        return FakeResponse({"releases": []})

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    assert create_catalog().search("Unknown", "Unknown") == ()


def test_musicbrainz_catalog_implements_the_album_catalog_port() -> None:
    assert isinstance(create_catalog(), AlbumCatalog)


def test_search_waits_between_musicbrainz_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            FakeResponse({"releases": [{"id": "release-id"}]}),
            FakeResponse(
                {
                    "title": "Album",
                    "artist-credit": [{"artist": {"name": "Artist"}}],
                    "media": [{"tracks": [{"position": 1, "title": "Song"}]}],
                }
            ),
        ]
    )
    delays: list[float] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        return next(responses)

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    MusicBrainzCatalog(sleep=delays.append).search("Artist", "Album")

    assert delays == [1.0]


def test_search_retries_after_musicbrainz_returns_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: Iterator[FakeResponse | HTTPError] = iter(
        [
            HTTPError(
                "https://musicbrainz.org/ws/2/release/",
                503,
                "Service Unavailable",
                Message(),
                None,
            ),
            FakeResponse({"releases": []}),
        ]
    )
    delays: list[float] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        response = next(responses)
        if isinstance(response, HTTPError):
            raise response
        return response

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    assert MusicBrainzCatalog(sleep=delays.append).search("Artist", "Album") == ()
    assert delays == [1.0]


def test_search_honors_retry_after_from_musicbrainz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = Message()
    headers["Retry-After"] = "3"
    responses: Iterator[FakeResponse | HTTPError] = iter(
        [
            HTTPError(
                "https://musicbrainz.org/ws/2/release/",
                503,
                "Service Unavailable",
                headers,
                None,
            ),
            FakeResponse({"releases": []}),
        ]
    )
    delays: list[float] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        response = next(responses)
        if isinstance(response, HTTPError):
            raise response
        return response

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    assert MusicBrainzCatalog(sleep=delays.append).search("Artist", "Album") == ()
    assert delays == [3.0]


@pytest.mark.parametrize(
    "retry_after",
    ["Wed, 21 Oct 2015 07:28:00 GMT", "not a valid retry delay"],
)
def test_search_uses_a_safe_fallback_for_past_or_invalid_retry_after(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str,
) -> None:
    headers = Message()
    headers["Retry-After"] = retry_after
    responses: Iterator[FakeResponse | HTTPError] = iter(
        [
            HTTPError(
                "https://musicbrainz.org/ws/2/release/",
                503,
                "Service Unavailable",
                headers,
                None,
            ),
            FakeResponse({"releases": []}),
        ]
    )
    delays: list[float] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        response = next(responses)
        if isinstance(response, HTTPError):
            raise response
        return response

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    assert MusicBrainzCatalog(sleep=delays.append).search("Artist", "Album") == ()
    assert delays == [1.0]


def test_search_stops_after_the_retry_limit_with_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        raise HTTPError(
            request.full_url,
            503,
            "Service Unavailable",
            Message(),
            None,
        )

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    catalog = MusicBrainzCatalog(sleep=delays.append)
    with pytest.raises(MusicBrainzUnavailableError, match="after 3 attempts"):
        catalog.search("Artist", "Album")

    assert delays == [1.0, 2.0]


@pytest.mark.parametrize("payload", [[], {}, {"releases": "invalid"}])
def test_search_handles_responses_without_a_release_list(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        return FakeResponse(payload)

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    assert create_catalog().search("Artist", "Album") == ()


def test_search_skips_release_entries_without_an_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        return FakeResponse({"releases": [None, {}, {"id": ""}]})

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    assert create_catalog().search("Artist", "Album") == ()


def test_search_ignores_release_details_without_tracks_or_artist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            FakeResponse({"releases": [{"id": "incomplete"}]}),
            FakeResponse(
                {
                    "title": "Album",
                    "artist-credit": [],
                    "media": [{"tracks": [{"position": 1, "title": "Song"}]}],
                }
            ),
        ]
    )

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        return next(responses)

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    assert create_catalog().search("Artist", "Album") == ()


def test_search_ignores_release_details_without_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            FakeResponse({"releases": [{"id": "missing-media"}]}),
            FakeResponse(
                {
                    "title": "Album",
                    "artist-credit": [{"artist": {"name": "Artist"}}],
                    "media": None,
                }
            ),
        ]
    )

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        return next(responses)

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    assert create_catalog().search("Artist", "Album") == ()


def test_search_keeps_valid_tracks_and_joins_artist_credits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            FakeResponse({"releases": [{"id": "various-artists"}]}),
            FakeResponse(
                {
                    "title": "Compilation",
                    "artist-credit": [
                        None,
                        {"artist": {"name": "Artist One"}, "joinphrase": " & "},
                        {"artist": {"name": "Artist Two"}},
                    ],
                    "media": [
                        None,
                        {
                            "tracks": [
                                None,
                                {"position": "invalid", "title": "Ignored"},
                                {"position": 1, "title": "Valid song"},
                            ]
                        },
                    ],
                }
            ),
        ]
    )

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        return next(responses)

    monkeypatch.setattr("ribelle.infrastructure.musicbrainz_catalog.urlopen", fake_urlopen)

    assert create_catalog().search("Artist One", "Compilation") == (
        AlbumMetadata(
            artist="Artist One & Artist Two",
            title="Compilation",
            year=None,
            tracks=(TrackMetadata(number=1, title="Valid song", disc_number=2),),
        ),
    )
