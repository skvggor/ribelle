from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from textual.app import App
from textual.content import Content
from textual.widgets import Button, Input, OptionList, ProgressBar, Select, Static

from ribelle.domain.album_metadata import AlbumMetadata, TrackMetadata
from ribelle.presentation.ribelle_app import RibelleApp


class FakeDiscReader:
    def __init__(
        self,
        track_count: int = 1,
        devices: tuple[str, ...] = ("/dev/sr0",),
        error: OSError | None = None,
    ) -> None:
        self._track_count = track_count
        self._devices = devices
        self._error = error
        self.inspected_devices: list[str] = []

    def discover_devices(self, device_directory: Path = Path("/dev")) -> tuple[str, ...]:
        return self._devices

    def track_count(self, device: str) -> int:
        self.inspected_devices.append(device)
        if self._error is not None:
            raise self._error
        return self._track_count

    def rip_track(self, device: str, track_number: int, wave_path: Path) -> None:
        raise AssertionError("The test app must use its fake album ripper")


class FakeCatalog:
    def __init__(
        self,
        albums: tuple[AlbumMetadata, ...],
        error: RuntimeError | None = None,
    ) -> None:
        self._albums = albums
        self._error = error
        self.searches: list[tuple[str, str]] = []

    def search(self, artist: str, title: str) -> tuple[AlbumMetadata, ...]:
        self.searches.append((artist, title))
        if self._error is not None:
            raise self._error
        return self._albums


class FakeAlbumRipper:
    def __init__(self, error: OSError | None = None) -> None:
        self.rips: list[tuple[AlbumMetadata, str, Path, int]] = []
        self._error = error

    def rip(
        self,
        album: AlbumMetadata,
        device: str,
        output_directory: Path,
        disc_number: int = 1,
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[Path, ...]:
        if self._error is not None:
            raise self._error
        self.rips.append((album, device, output_directory, disc_number))
        if on_progress is not None:
            on_progress("Finished track 01: Song")
        return (output_directory / "01 - Song.flac",)


def album_with_one_track(title: str = "Album") -> AlbumMetadata:
    return AlbumMetadata(
        artist="Artist",
        title=title,
        year="2024",
        tracks=(TrackMetadata(1, "Song"),),
    )


def static_text(app: App[None], selector: str) -> str:
    rendered_content = app.query_one(selector, Static).render()
    assert isinstance(rendered_content, Content)
    return rendered_content.plain


@pytest.mark.asyncio
async def test_tui_starts_with_search_fields_and_detected_drive() -> None:
    disc_reader = FakeDiscReader(devices=("/dev/sr0", "/dev/sr1"))
    catalog = FakeCatalog(())
    app: App[None] = RibelleApp(disc_reader, catalog, FakeAlbumRipper())

    async with app.run_test() as pilot:
        assert app.query_one("#artist-input", Input).value == ""
        assert app.query_one("#album-input", Input).value == ""
        assert app.query_one("#drive-select", Select).value == "/dev/sr0"
        assert not catalog.searches
        await pilot.pause()


@pytest.mark.asyncio
async def test_tui_uses_the_default_device_when_no_drive_is_detected() -> None:
    app = RibelleApp(FakeDiscReader(devices=()), FakeCatalog(()), FakeAlbumRipper())

    async with app.run_test() as pilot:
        assert app.query_one("#drive-select", Select).value == "/dev/sr0"
        await pilot.pause()


@pytest.mark.asyncio
async def test_tui_automatically_rips_a_single_matching_release(tmp_path: Path) -> None:
    album = album_with_one_track()
    disc_reader = FakeDiscReader()
    catalog = FakeCatalog((album,))
    ripper = FakeAlbumRipper()
    app = RibelleApp(
        disc_reader,
        catalog,
        ripper,
        initial_artist="Artist",
        initial_album="Album",
        output_directory=tmp_path,
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        assert disc_reader.inspected_devices == ["/dev/sr0"]
        assert catalog.searches == [("Artist", "Album")]
        assert ripper.rips == [(album, "/dev/sr0", tmp_path, 1)]
        assert "complete" in static_text(app, "#status").lower()
        assert app.query_one("#rip-progress", ProgressBar).progress == 1


@pytest.mark.asyncio
async def test_tui_shows_multiple_editions_and_waits_for_the_user_to_choose() -> None:
    first_album = album_with_one_track("First pressing")
    second_album = album_with_one_track("Second pressing")
    ripper = FakeAlbumRipper()
    app = RibelleApp(
        FakeDiscReader(),
        FakeCatalog((first_album, second_album)),
        ripper,
        initial_artist="Artist",
        initial_album="Album",
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        assert len(app.query_one("#release-options", OptionList).options) == 2
        assert app.query_one("#rip-button", Button).disabled
        assert not ripper.rips

        release_options = app.query_one("#release-options", OptionList)
        release_options.focus()
        release_options.highlighted = 0
        await pilot.press("enter")
        await pilot.pause()
        assert not app.query_one("#rip-button", Button).disabled

        await pilot.click("#rip-button")
        await pilot.pause(0.1)

        assert ripper.rips == [(first_album, "/dev/sr0", Path.home() / "Music", 1)]
        assert "complete" in static_text(app, "#status").lower()


@pytest.mark.asyncio
async def test_tui_reports_when_the_drive_contains_no_audio_tracks() -> None:
    catalog = FakeCatalog(())
    app = RibelleApp(
        FakeDiscReader(track_count=0),
        catalog,
        FakeAlbumRipper(),
        initial_artist="Artist",
        initial_album="Album",
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        assert not catalog.searches
        assert "no audio tracks" in static_text(app, "#status").lower()


@pytest.mark.asyncio
async def test_tui_reports_when_musicbrainz_has_no_matching_release() -> None:
    catalog = FakeCatalog(())
    app = RibelleApp(FakeDiscReader(), catalog, FakeAlbumRipper())

    async with app.run_test() as pilot:
        await pilot.click("#artist-input")
        await pilot.press(*"Artist")
        await pilot.click("#album-input")
        await pilot.press(*"Missing")
        await pilot.click("#search-button")
        await pilot.pause(0.1)

        assert catalog.searches == [("Artist", "Missing")]
        assert "no matching release" in static_text(app, "#status").lower()


@pytest.mark.asyncio
async def test_tui_shows_catalog_errors_without_exiting() -> None:
    catalog = FakeCatalog((), error=RuntimeError("MusicBrainz unavailable"))
    app = RibelleApp(
        FakeDiscReader(),
        catalog,
        FakeAlbumRipper(),
        initial_artist="Artist",
        initial_album="Album",
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        assert "musicbrainz unavailable" in static_text(app, "#status").lower()
        assert app.is_running


@pytest.mark.asyncio
async def test_tui_reports_rip_errors_and_allows_another_attempt() -> None:
    album = album_with_one_track()
    app = RibelleApp(
        FakeDiscReader(),
        FakeCatalog((album,)),
        FakeAlbumRipper(error=OSError("drive read failed")),
        initial_artist="Artist",
        initial_album="Album",
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        assert "drive read failed" in static_text(app, "#status").lower()
        assert app.query_one("#search-button", Button).disabled is False


@pytest.mark.asyncio
async def test_tui_honors_the_reduced_motion_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIBELLE_REDUCED_MOTION", "1")
    app = RibelleApp(FakeDiscReader(), FakeCatalog(()), FakeAlbumRipper())

    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.animation_level == "none"
        assert not app.query_one("#loading-indicator").display


@pytest.mark.asyncio
async def test_tui_requires_both_search_fields_before_calling_the_catalog() -> None:
    catalog = FakeCatalog(())
    app = RibelleApp(FakeDiscReader(), catalog, FakeAlbumRipper())

    async with app.run_test() as pilot:
        await pilot.click("#search-button")
        assert not catalog.searches
        assert "enter both" in static_text(app, "#status").lower()


@pytest.mark.asyncio
async def test_tui_applies_high_contrast_and_reduced_motion_preferences() -> None:
    app = RibelleApp(
        FakeDiscReader(),
        FakeCatalog(()),
        FakeAlbumRipper(),
        high_contrast=True,
        reduced_motion=True,
    )

    async with app.run_test(size=(60, 18)) as pilot:
        await pilot.pause()

        assert app.theme == "ansi-dark"
        assert app.animation_level == "none"
        assert not app.query_one("#loading-indicator").display
