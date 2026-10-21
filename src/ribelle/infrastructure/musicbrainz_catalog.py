"""MusicBrainz implementation of the album catalog port."""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import sleep as sleep_for_seconds
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ribelle.application.album_catalog import AlbumCatalog
from ribelle.domain.album_metadata import AlbumMetadata, TrackMetadata

MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2"
MUSICBRAINZ_USER_AGENT = "ribelle/0.1.0 (https://musicbrainz.org)"
REQUEST_TIMEOUT_SECONDS = 15
MAX_RELEASE_RESULTS = 5
MINIMUM_REQUEST_INTERVAL_SECONDS = 1.0
MAX_REQUEST_ATTEMPTS = 3
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


class MusicBrainzUnavailableError(RuntimeError):
    """Raised when MusicBrainz remains unavailable after bounded retries."""


class MusicBrainzCatalog(AlbumCatalog):
    """Search MusicBrainz and retrieve each candidate's track list."""

    def __init__(self, sleep: Callable[[float], None] = sleep_for_seconds) -> None:
        self._sleep = sleep
        self._has_made_request = False

    def search(self, artist: str, title: str) -> tuple[AlbumMetadata, ...]:
        query = f'artist:"{artist}" AND release:"{title}"'
        parameters = urlencode({"query": query, "fmt": "json", "limit": MAX_RELEASE_RESULTS})
        search_results = self._request_json(f"/release/?{parameters}")
        releases = search_results.get("releases")
        if not isinstance(releases, list):
            return ()

        albums: list[AlbumMetadata] = []
        for release in releases:
            if not isinstance(release, Mapping):
                continue
            release_identifier = release.get("id")
            if not isinstance(release_identifier, str) or not release_identifier:
                continue
            release_path = (
                f"/release/{quote(release_identifier, safe='')}?inc=artists+recordings&fmt=json"
            )
            release_details = self._request_json(release_path)
            album = self._parse_album(release_details)
            if album is not None:
                albums.append(album)

        return tuple(albums)

    def _request_json(self, path: str) -> Mapping[str, object]:
        request = Request(
            f"{MUSICBRAINZ_API_URL}{path}",
            headers={"User-Agent": MUSICBRAINZ_USER_AGENT, "Accept": "application/json"},
        )
        attempt = 0
        while True:
            if self._has_made_request:
                self._sleep(MINIMUM_REQUEST_INTERVAL_SECONDS)
            self._has_made_request = True
            try:
                with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    payload = json.loads(response.read())
                if not isinstance(payload, Mapping):
                    return {}
                return payload
            except HTTPError as error:
                if error.code not in RETRYABLE_HTTP_STATUSES:
                    raise
                attempt += 1
                if attempt >= MAX_REQUEST_ATTEMPTS:
                    raise MusicBrainzUnavailableError(
                        f"MusicBrainz returned HTTP {error.code} after "
                        f"{MAX_REQUEST_ATTEMPTS} attempts. Please retry later."
                    ) from error
                retry_delay = _retry_delay_seconds(error, attempt - 1)
                self._sleep(max(MINIMUM_REQUEST_INTERVAL_SECONDS, retry_delay))
                self._has_made_request = False

    @classmethod
    def _parse_album(cls, release: Mapping[str, object]) -> AlbumMetadata | None:
        artist = cls._artist_name(release.get("artist-credit"))
        title = release.get("title")
        if not artist or not isinstance(title, str) or not title.strip():
            return None

        tracks = cls._parse_tracks(release.get("media"))
        if not tracks:
            return None
        return AlbumMetadata(
            artist=artist,
            title=title,
            year=cls._year(release.get("date")),
            tracks=tracks,
        )

    @classmethod
    def _parse_tracks(cls, media: object) -> tuple[TrackMetadata, ...]:
        if not isinstance(media, list):
            return ()
        tracks: list[TrackMetadata] = []
        for disc_number, medium in enumerate(media, start=1):
            tracks.extend(cls._parse_medium_tracks(medium, disc_number))
        return tuple(tracks)

    @staticmethod
    def _parse_medium_tracks(medium: object, disc_number: int) -> tuple[TrackMetadata, ...]:
        if not isinstance(medium, Mapping):
            return ()
        medium_tracks = medium.get("tracks")
        if not isinstance(medium_tracks, list):
            return ()

        tracks: list[TrackMetadata] = []
        for track in medium_tracks:
            if not isinstance(track, Mapping):
                continue
            number = track.get("position")
            title = track.get("title")
            if isinstance(number, int) and isinstance(title, str) and title.strip():
                tracks.append(TrackMetadata(number=number, title=title, disc_number=disc_number))
        return tuple(tracks)

    @staticmethod
    def _year(release_date: object) -> str | None:
        if isinstance(release_date, str) and release_date:
            return release_date[:4]
        return None

    @staticmethod
    def _artist_name(artist_credit: object) -> str:
        if not isinstance(artist_credit, list):
            return ""
        artist_parts: list[str] = []
        for credit in artist_credit:
            if not isinstance(credit, Mapping):
                continue
            artist = credit.get("artist")
            if isinstance(artist, Mapping):
                name = artist.get("name")
                if isinstance(name, str):
                    artist_parts.append(name)
            join_phrase = credit.get("joinphrase")
            if isinstance(join_phrase, str):
                artist_parts.append(join_phrase)
        return "".join(artist_parts).strip()


def _retry_delay_seconds(error: HTTPError, retry_number: int) -> float:
    retry_after = error.headers.get("Retry-After") if error.headers is not None else None
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_date = parsedate_to_datetime(retry_after)
            except (TypeError, ValueError, OverflowError):
                retry_date = None
            if retry_date is not None:
                if retry_date.tzinfo is None:
                    retry_date = retry_date.replace(tzinfo=UTC)
                current_time = datetime.now(tz=retry_date.tzinfo)
                return max(0.0, (retry_date - current_time).total_seconds())
    return float(2**retry_number)
