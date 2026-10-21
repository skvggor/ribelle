"""Coordinate lossless extraction and tagging for one album disc."""

import re
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from ribelle.domain.album_metadata import AlbumMetadata, TrackMetadata


class AudioDiscReader(Protocol):
    """Inspect an audio disc and extract tracks to uncompressed waveform files."""

    def discover_devices(self, device_directory: Path = Path("/dev")) -> tuple[str, ...]: ...

    def track_count(self, device: str) -> int: ...

    def rip_track(self, device: str, track_number: int, wave_path: Path) -> None: ...


class FlacEncoder(Protocol):
    """Encode an uncompressed waveform as FLAC."""

    def encode(self, wave_path: Path, flac_path: Path) -> None: ...


class TrackTagWriter(Protocol):
    """Write album and track metadata into a FLAC file."""

    def tag(self, file_path: Path, album: AlbumMetadata, track: TrackMetadata) -> None: ...


class AlbumRipperPort(Protocol):
    """Port for application workflows that rip and tag an album disc."""

    def rip(
        self,
        album: AlbumMetadata,
        device: str,
        output_directory: Path,
        disc_number: int = 1,
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[Path, ...]: ...


class AlbumRipper:
    """Rip, encode, tag, and safely place tracks from one disc."""

    def __init__(
        self,
        disc_reader: AudioDiscReader,
        flac_encoder: FlacEncoder,
        tag_writer: TrackTagWriter,
    ) -> None:
        self._disc_reader = disc_reader
        self._flac_encoder = flac_encoder
        self._tag_writer = tag_writer

    def rip(
        self,
        album: AlbumMetadata,
        device: str,
        output_directory: Path,
        disc_number: int = 1,
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[Path, ...]:
        tracks = tuple(track for track in album.tracks if track.disc_number == disc_number)
        if not tracks:
            raise ValueError(f"Release does not contain disc {disc_number}")

        album_directory = (
            output_directory.expanduser()
            / _safe_path_component(album.artist)
            / _safe_path_component(album.title)
        )
        multiple_discs = len({track.disc_number for track in album.tracks}) > 1
        disc_directory = (
            album_directory / f"Disc {disc_number}" if multiple_discs else album_directory
        )
        output_files = tuple(
            disc_directory / f"{track.number:02d} - {_safe_path_component(track.title)}.flac"
            for track in tracks
        )
        existing_files = tuple(path for path in output_files if path.exists())
        if existing_files:
            raise FileExistsError(f"Output file already exists: {existing_files[0]}")

        disc_directory.mkdir(parents=True, exist_ok=True)
        for track, output_file in zip(tracks, output_files, strict=True):
            with TemporaryDirectory(prefix=".ribelle-", dir=disc_directory) as temporary_path:
                temporary_directory = Path(temporary_path)
                wave_path = temporary_directory / f"{track.number:02d}.wav"
                flac_path = temporary_directory / f"{track.number:02d}.flac"
                if on_progress is not None:
                    on_progress(f"Reading track {track.number:02d}: {track.title}")
                self._disc_reader.rip_track(device, track.number, wave_path)
                if on_progress is not None:
                    on_progress(f"Encoding track {track.number:02d}: {track.title}")
                self._flac_encoder.encode(wave_path, flac_path)
                if on_progress is not None:
                    on_progress(f"Tagging track {track.number:02d}: {track.title}")
                self._tag_writer.tag(flac_path, album, track)
                flac_path.replace(output_file)
                if on_progress is not None:
                    on_progress(f"Finished track {track.number:02d}: {track.title}")

        return output_files


def _safe_path_component(value: str) -> str:
    safe_value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return safe_value or "Unknown"
