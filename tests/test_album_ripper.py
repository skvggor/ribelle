from pathlib import Path

import pytest

from ribelle.application.rip_album import AlbumRipper
from ribelle.domain.album_metadata import AlbumMetadata, TrackMetadata


class FakeDiscReader:
    def __init__(self) -> None:
        self.track_numbers: list[int] = []

    def track_count(self, device: str) -> int:
        return 2

    def discover_devices(self, device_directory: Path = Path("/dev")) -> tuple[str, ...]:
        return ("/dev/sr0",)

    def rip_track(self, device: str, track_number: int, wave_path: Path) -> None:
        self.track_numbers.append(track_number)
        wave_path.write_bytes(f"wave {track_number}".encode())


class FakeFlacEncoder:
    def encode(self, wave_path: Path, flac_path: Path) -> None:
        flac_path.write_bytes(wave_path.read_bytes())


class FakeTagWriter:
    def __init__(self) -> None:
        self.tagged_tracks: list[tuple[int, int]] = []

    def tag(self, file_path: Path, album: AlbumMetadata, track: TrackMetadata) -> None:
        assert file_path.exists()
        self.tagged_tracks.append((track.disc_number, track.number))


def create_album(tracks: tuple[TrackMetadata, ...]) -> AlbumMetadata:
    return AlbumMetadata(artist="Artist", title="Album", year="2024", tracks=tracks)


def create_ripper() -> tuple[AlbumRipper, FakeDiscReader, FakeTagWriter]:
    disc_reader = FakeDiscReader()
    tag_writer = FakeTagWriter()
    return AlbumRipper(disc_reader, FakeFlacEncoder(), tag_writer), disc_reader, tag_writer


def test_rips_and_tags_tracks_in_order_as_flac_files(tmp_path: Path) -> None:
    album = create_album((TrackMetadata(2, "Second"), TrackMetadata(1, "First")))
    ripper, disc_reader, tag_writer = create_ripper()

    output_files = ripper.rip(album, "/dev/sr0", tmp_path)

    assert [path.name for path in output_files] == ["01 - First.flac", "02 - Second.flac"]
    assert all(path.is_file() for path in output_files)
    assert disc_reader.track_numbers == [1, 2]
    assert tag_writer.tagged_tracks == [(1, 1), (1, 2)]


def test_reports_each_track_processing_stage_when_progress_is_requested(
    tmp_path: Path,
) -> None:
    ripper, _, _ = create_ripper()
    progress_messages: list[str] = []

    ripper.rip(
        create_album((TrackMetadata(1, "First song"),)),
        "/dev/sr0",
        tmp_path,
        on_progress=progress_messages.append,
    )

    assert progress_messages == [
        "Reading track 01: First song",
        "Encoding track 01: First song",
        "Tagging track 01: First song",
        "Finished track 01: First song",
    ]


def test_rips_only_the_selected_disc_into_a_separate_folder(tmp_path: Path) -> None:
    album = create_album(
        (
            TrackMetadata(1, "First disc", disc_number=1),
            TrackMetadata(1, "Second disc", disc_number=2),
        )
    )
    ripper, disc_reader, tag_writer = create_ripper()

    output_files = ripper.rip(album, "/dev/sr0", tmp_path, disc_number=2)

    assert output_files[0].parent.name == "Disc 2"
    assert output_files[0].name == "01 - Second disc.flac"
    assert disc_reader.track_numbers == [1]
    assert tag_writer.tagged_tracks == [(2, 1)]


def test_rip_rejects_a_disc_not_present_in_the_release(tmp_path: Path) -> None:
    ripper, disc_reader, _ = create_ripper()
    album = create_album((TrackMetadata(1, "Song"),))

    with pytest.raises(ValueError, match="does not contain disc 2"):
        ripper.rip(album, "/dev/sr0", tmp_path, disc_number=2)

    assert disc_reader.track_numbers == []
    assert not (tmp_path / "Artist").exists()


def test_rip_does_not_overwrite_an_existing_track(tmp_path: Path) -> None:
    album = create_album((TrackMetadata(1, "First"),))
    existing_file = tmp_path / "Artist" / "Album" / "01 - First.flac"
    existing_file.parent.mkdir(parents=True)
    existing_file.write_bytes(b"existing backup")
    ripper, disc_reader, _ = create_ripper()

    with pytest.raises(FileExistsError, match="already exists"):
        ripper.rip(album, "/dev/sr0", tmp_path)

    assert existing_file.read_bytes() == b"existing backup"
    assert disc_reader.track_numbers == []


def test_rip_sanitizes_metadata_before_creating_output_paths(tmp_path: Path) -> None:
    album = AlbumMetadata(
        artist="../Artist",
        title="Album/Part",
        year=None,
        tracks=(TrackMetadata(1, "Song/Part"),),
    )
    ripper, _, _ = create_ripper()

    output_files = ripper.rip(album, "/dev/sr0", tmp_path)

    assert output_files[0].is_relative_to(tmp_path)
    assert output_files[0].name == "01 - Song_Part.flac"
