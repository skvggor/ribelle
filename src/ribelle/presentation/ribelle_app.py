"""Textual application for selecting and backing up audio CDs."""

import asyncio
import os
from pathlib import Path
from subprocess import CalledProcessError

from mutagen import MutagenError
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    OptionList,
    ProgressBar,
    Select,
    Static,
)
from textual.widgets.option_list import Option

from ribelle.application.album_catalog import AlbumCatalog
from ribelle.application.release_matching import find_matching_discs
from ribelle.application.rip_album import AlbumRipperPort, AudioDiscReader
from ribelle.domain.album_metadata import AlbumMetadata

RELEASE_PANEL_SELECTOR = "#release-panel"
PROGRESS_PANEL_SELECTOR = "#progress-panel"
RIP_BUTTON_SELECTOR = "#rip-button"
SEARCH_BUTTON_SELECTOR = "#search-button"
STATUS_SELECTOR = "#status"


class RibelleApp(App[None]):
    """Guide the user from CD detection to tagged FLAC files."""

    TITLE = "Ribelle"
    SUB_TITLE = "Lossless audio CD archive"
    CSS_PATH = "ribelle.tcss"
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (72, "-wide")]
    BINDINGS = [
        ("ctrl+r", "search", "Search releases"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self,
        disc_reader: AudioDiscReader,
        catalog: AlbumCatalog,
        ripper: AlbumRipperPort,
        *,
        initial_artist: str | None = None,
        initial_album: str | None = None,
        initial_device: str | None = None,
        output_directory: Path = Path("~/Music"),
        high_contrast: bool = False,
        reduced_motion: bool = False,
    ) -> None:
        super().__init__()
        self._disc_reader = disc_reader
        self._catalog = catalog
        self._ripper = ripper
        available_devices = disc_reader.discover_devices()
        if initial_device is not None and initial_device not in available_devices:
            available_devices = (initial_device, *available_devices)
        self._available_devices = available_devices or ("/dev/sr0",)
        self._initial_artist = initial_artist or ""
        self._initial_album = initial_album or ""
        self._initial_device = initial_device or self._available_devices[0]
        self._output_directory = output_directory.expanduser()
        self._matching_discs: tuple[tuple[AlbumMetadata, int], ...] = ()
        self._selected_release_index: int | None = None
        self._busy = False
        self._high_contrast = high_contrast
        self._reduced_motion = (
            reduced_motion
            or os.environ.get("RIBELLE_REDUCED_MOTION") == "1"
            or self.animation_level == "none"
        )
        if high_contrast:
            self.theme = "ansi-dark"
        if self._reduced_motion:
            self.animation_level = "none"

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="main-scroll"):
            with Vertical(id="main-panel"):
                yield Static("Ribelle", id="app-title")
                yield Static(
                    "Create a lossless FLAC backup of an audio CD.",
                    id="app-description",
                )
                with Vertical(id="search-panel"):
                    with Horizontal(classes="field-row"):
                        yield Input(
                            value=self._initial_artist,
                            placeholder="Artist",
                            id="artist-input",
                        )
                        yield Input(
                            value=self._initial_album,
                            placeholder="Album title",
                            id="album-input",
                        )
                    with Horizontal(classes="field-row"):
                        yield Select(
                            [(device, device) for device in self._available_devices],
                            prompt="CD drive",
                            value=self._initial_device,
                            allow_blank=False,
                            id="drive-select",
                        )
                        yield Input(
                            value=str(self._output_directory),
                            placeholder="Save to (~/Music)",
                            id="output-input",
                        )
                    yield Button("Find matching releases", id="search-button", variant="primary")
                yield LoadingIndicator(id="loading-indicator")
                yield Static("Ready. Insert an audio CD to begin.", id="status")
                with Vertical(id="release-panel"):
                    yield Static("Matching release editions", id="release-heading")
                    yield OptionList(id="release-options", markup=False)
                    yield Button(
                        "Rip selected edition",
                        id="rip-button",
                        variant="primary",
                        disabled=True,
                    )
                with Vertical(id="progress-panel"):
                    yield Static("Ripping audio tracks", id="progress-heading")
                    yield ProgressBar(
                        total=1,
                        show_eta=False,
                        id="rip-progress",
                    )
                    yield Static("", id="destination-status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(RELEASE_PANEL_SELECTOR).display = False
        self.query_one(PROGRESS_PANEL_SELECTOR).display = False
        self.query_one("#loading-indicator").display = False
        if self._initial_artist and self._initial_album:
            self.run_worker(
                self._search_and_rip(
                    self._initial_artist,
                    self._initial_album,
                    self._initial_device,
                    self._output_directory,
                ),
                group="album-workflow",
                exclusive=True,
                exit_on_error=False,
            )

    @on(Button.Pressed, SEARCH_BUTTON_SELECTOR)
    async def search_button_pressed(self) -> None:
        artist = self.query_one("#artist-input", Input).value.strip()
        album_title = self.query_one("#album-input", Input).value.strip()
        selected_device = self.query_one("#drive-select", Select).value
        if not artist or not album_title:
            self._show_error("Enter both an artist and an album title.")
            return
        if not isinstance(selected_device, str):
            self._show_error("Select a CD drive before searching.")
            return
        output_directory = Path(self.query_one("#output-input", Input).value).expanduser()
        await self._search_and_rip(artist, album_title, selected_device, output_directory)

    @on(Button.Pressed, RIP_BUTTON_SELECTOR)
    async def rip_button_pressed(self) -> None:
        if self._selected_release_index is None:
            return
        album, disc_number = self._matching_discs[self._selected_release_index]
        selected_device = self.query_one("#drive-select", Select).value
        if not isinstance(selected_device, str):
            self._show_error("Select a CD drive before ripping.")
            return
        output_directory = Path(self.query_one("#output-input", Input).value).expanduser()
        await self._rip_selected(album, disc_number, selected_device, output_directory)

    @on(OptionList.OptionSelected, "#release-options")
    def release_selected(self, event: OptionList.OptionSelected) -> None:
        self._selected_release_index = event.option_index
        self.query_one(RIP_BUTTON_SELECTOR, Button).disabled = False

    async def action_search(self) -> None:
        if not self._busy:
            await self.search_button_pressed()

    async def _search_and_rip(
        self,
        artist: str,
        album_title: str,
        device: str,
        output_directory: Path,
    ) -> None:
        if self._busy:
            return
        self._busy = True
        self._matching_discs = ()
        self._selected_release_index = None
        self.query_one(RELEASE_PANEL_SELECTOR).display = False
        self.query_one(PROGRESS_PANEL_SELECTOR).display = False
        self.query_one(RIP_BUTTON_SELECTOR, Button).disabled = True
        self.query_one(SEARCH_BUTTON_SELECTOR, Button).disabled = True
        self._set_loading(True)
        try:
            self.query_one(STATUS_SELECTOR, Static).update("Checking the audio CD...")
            disc_track_count = await asyncio.to_thread(self._disc_reader.track_count, device)
            if disc_track_count < 1:
                self._show_error("No audio tracks were detected in the selected drive.")
                return

            self.query_one(STATUS_SELECTOR, Static).update("Searching MusicBrainz...")
            albums = await asyncio.to_thread(self._catalog.search, artist, album_title)
            matching_discs = find_matching_discs(albums, disc_track_count)
            if not matching_discs:
                self._show_error(
                    f"No matching release has a disc with {disc_track_count} tracks. "
                    "Check the search terms or drive selection."
                )
                return

            self._matching_discs = matching_discs
            if len(matching_discs) == 1:
                matched_album, disc_number = matching_discs[0]
                await self._rip_selected(matched_album, disc_number, device, output_directory)
                return
            self._show_release_choices(matching_discs)
        except (MutagenError, OSError, RuntimeError, ValueError) as error:
            self._show_error(_error_message(error))
        finally:
            self._busy = False
            self._set_loading(False)
            self.query_one(SEARCH_BUTTON_SELECTOR, Button).disabled = False

    async def _rip_selected(
        self,
        album: AlbumMetadata,
        disc_number: int,
        device: str,
        output_directory: Path,
    ) -> None:
        already_busy = self._busy
        self._busy = True
        self.query_one(SEARCH_BUTTON_SELECTOR, Button).disabled = True
        self.query_one(RIP_BUTTON_SELECTOR, Button).disabled = True
        disc_track_count = sum(track.disc_number == disc_number for track in album.tracks)
        progress = self.query_one("#rip-progress", ProgressBar)
        progress.update(total=disc_track_count, progress=0)
        self.query_one(RELEASE_PANEL_SELECTOR).display = False
        self.query_one(PROGRESS_PANEL_SELECTOR).display = True
        self.query_one(STATUS_SELECTOR, Static).update(
            "Ripping audio tracks. Progress advances after each track."
        )
        self._set_loading(True)

        def on_progress(message: str) -> None:
            if message.startswith("Finished track "):
                self.call_from_thread(progress.advance)

        try:
            ripped_files = await asyncio.to_thread(
                self._ripper.rip,
                album,
                device,
                output_directory,
                disc_number,
                on_progress,
            )
        except (CalledProcessError, MutagenError, OSError, RuntimeError, ValueError) as error:
            self.query_one(PROGRESS_PANEL_SELECTOR).display = False
            self.query_one(RELEASE_PANEL_SELECTOR).display = bool(self._matching_discs)
            self._show_error(_error_message(error))
            return
        finally:
            self._set_loading(False)
            self._busy = already_busy
            self.query_one(SEARCH_BUTTON_SELECTOR, Button).disabled = already_busy
            self.query_one(RIP_BUTTON_SELECTOR, Button).disabled = (
                already_busy or self._selected_release_index is None
            )

        self.query_one(STATUS_SELECTOR, Static).update(
            f"Backup complete: {len(ripped_files)} FLAC files saved."
        )
        self.query_one("#destination-status", Static).update(str(output_directory))

    def _show_release_choices(
        self,
        matching_discs: tuple[tuple[AlbumMetadata, int], ...],
    ) -> None:
        release_options = self.query_one("#release-options", OptionList)
        release_options.clear_options()
        for album, disc_number in matching_discs:
            track_count = sum(track.disc_number == disc_number for track in album.tracks)
            year = album.year or "year unknown"
            release_options.add_option(
                Option(
                    f"{album.artist} — {album.title} ({year}) — "
                    f"Disc {disc_number} ({track_count} tracks)"
                )
            )
        self.query_one(RELEASE_PANEL_SELECTOR).display = True
        self.query_one(RIP_BUTTON_SELECTOR, Button).disabled = True
        self.query_one(STATUS_SELECTOR, Static).update("Choose the matching release edition.")

    def _show_error(self, message: str) -> None:
        self.query_one(STATUS_SELECTOR, Static).update(f"Error: {message}")

    def _set_loading(self, is_loading: bool) -> None:
        loading_indicator = self.query_one("#loading-indicator", LoadingIndicator)
        loading_indicator.display = is_loading and not self._reduced_motion


def _error_message(error: Exception) -> str:
    if isinstance(error, CalledProcessError):
        command_output = error.stderr or error.stdout
        if isinstance(command_output, str) and command_output.strip():
            return command_output.strip()
    return str(error)
