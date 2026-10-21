from collections.abc import Sequence
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from ribelle.domain.album_metadata import AlbumMetadata, TrackMetadata
from ribelle.infrastructure.audio_cd import CdParanoiaDrive
from ribelle.infrastructure.flac_encoding import FlacEncoderAdapter
from ribelle.infrastructure.flac_tags import MutagenTagWriter


def test_cdparanoia_counts_audio_tracks_in_its_table_of_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_result = CompletedProcess(
        args=[],
        returncode=0,
        stdout="",
        stderr="  1.  20218 [04:29.43] 0 [00:00.00]\n  2.  18312 [04:03.12] 20218 [04:29.43]\n",
    )
    commands: list[Sequence[str]] = []

    def fake_run(
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> CompletedProcess[str]:
        commands.append(command)
        return command_result

    monkeypatch.setattr("ribelle.infrastructure.audio_cd.run", fake_run)

    assert CdParanoiaDrive().track_count("/dev/sr0") == 2
    assert commands == [["cdparanoia", "-Q", "-d", "/dev/sr0"]]


def test_cdparanoia_discovers_available_optical_drives(tmp_path: Path) -> None:
    (tmp_path / "sr1").touch()
    (tmp_path / "sr0").touch()
    (tmp_path / "cdrom").touch()

    assert CdParanoiaDrive().discover_devices(tmp_path) == (
        str(tmp_path / "sr0"),
        str(tmp_path / "sr1"),
    )


def test_cdparanoia_rips_a_track_to_the_requested_wave_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[Sequence[str]] = []
    invocation_options: list[tuple[bool, bool, bool]] = []

    def fake_run(
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> CompletedProcess[str]:
        commands.append(command)
        invocation_options.append((check, capture_output, text))
        return CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ribelle.infrastructure.audio_cd.run", fake_run)
    wave_path = tmp_path / "track.wav"

    CdParanoiaDrive().rip_track("/dev/sr0", 4, wave_path)

    assert commands == [["cdparanoia", "-d", "/dev/sr0", "4", str(wave_path)]]
    assert invocation_options == [(True, True, True)]


def test_flac_encoder_requests_lossless_best_compression(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[Sequence[str]] = []

    def fake_run(
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> CompletedProcess[str]:
        commands.append(command)
        return CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ribelle.infrastructure.flac_encoding.run", fake_run)
    wave_path = tmp_path / "track.wav"
    flac_path = tmp_path / "track.flac"

    FlacEncoderAdapter().encode(wave_path, flac_path)

    assert commands == [
        ["flac", "--silent", "--best", "--output-name", str(flac_path), str(wave_path)]
    ]


class FakeFlacFile(dict[str, str]):
    def __init__(self) -> None:
        super().__init__()
        self.saved = False

    def save(self) -> None:
        self.saved = True


def test_mutagen_writer_adds_album_disc_track_and_year_tags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    flac_file = FakeFlacFile()
    received_paths: list[Path] = []

    def fake_flac(path: Path) -> FakeFlacFile:
        received_paths.append(path)
        return flac_file

    monkeypatch.setattr("ribelle.infrastructure.flac_tags.FLAC", fake_flac)
    album = AlbumMetadata(
        artist="Artist",
        title="Album",
        year="2024",
        tracks=(
            TrackMetadata(1, "First disc"),
            TrackMetadata(1, "Song", disc_number=2),
            TrackMetadata(2, "Next", disc_number=2),
        ),
    )
    file_path = tmp_path / "song.flac"

    MutagenTagWriter().tag(file_path, album, album.tracks[1])

    assert received_paths == [file_path]
    assert flac_file == {
        "ARTIST": "Artist",
        "ALBUMARTIST": "Artist",
        "ALBUM": "Album",
        "TITLE": "Song",
        "TRACKNUMBER": "1",
        "TRACKTOTAL": "2",
        "DISCNUMBER": "2",
        "DISCTOTAL": "2",
        "DATE": "2024",
    }
    assert flac_file.saved


def test_mutagen_writer_omits_a_missing_release_year(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    flac_file = FakeFlacFile()
    monkeypatch.setattr("ribelle.infrastructure.flac_tags.FLAC", lambda path: flac_file)
    album = AlbumMetadata(
        artist="Artist",
        title="Album",
        year=None,
        tracks=(TrackMetadata(1, "Song"),),
    )

    MutagenTagWriter().tag(tmp_path / "song.flac", album, album.tracks[0])

    assert "DATE" not in flac_file


def test_mutagen_writer_rejects_a_file_that_is_not_readable_as_flac(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("ribelle.infrastructure.flac_tags.FLAC", lambda path: None)
    album = AlbumMetadata(
        artist="Artist",
        title="Album",
        year=None,
        tracks=(TrackMetadata(1, "Song"),),
    )
    tag_writer = MutagenTagWriter()

    with pytest.raises(ValueError, match="Could not read FLAC file"):
        tag_writer.tag(tmp_path / "invalid.flac", album, album.tracks[0])
