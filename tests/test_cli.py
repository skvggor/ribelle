from __future__ import annotations

from pathlib import Path

import pytest

from ribelle import cli


class FakeDiscReader:
    def discover_devices(self, device_directory: Path = Path("/dev")) -> tuple[str, ...]:
        return ("/dev/sr0",)

    def track_count(self, device: str) -> int:
        return 1

    def rip_track(self, device: str, track_number: int, wave_path: Path) -> None:
        return None


class FakeCatalog:
    def search(self, artist: str, title: str) -> tuple[()]:
        return ()


class FakeRipper:
    def __init__(self, *dependencies: object) -> None:
        self.dependencies = dependencies

    def rip(
        self,
        album: object,
        device: str,
        output_directory: Path,
        disc_number: int = 1,
        on_progress: object = None,
    ) -> tuple[Path, ...]:
        return ()


class FakeTextualApp:
    def __init__(self) -> None:
        self.dependencies: tuple[object, ...] = ()
        self.settings: dict[str, object] = {}
        self.started = False
        self.return_code = 0

    def configure(self, dependencies: tuple[object, ...], settings: dict[str, object]) -> None:
        self.dependencies = dependencies
        self.settings = settings

    def run(self) -> None:
        self.started = True


def install_fake_components(monkeypatch: pytest.MonkeyPatch, app: FakeTextualApp) -> None:
    monkeypatch.setattr(cli, "CdParanoiaDrive", FakeDiscReader)
    monkeypatch.setattr(cli, "MusicBrainzCatalog", FakeCatalog)
    monkeypatch.setattr(cli, "AlbumRipper", FakeRipper)

    def create_app(*dependencies: object, **settings: object) -> FakeTextualApp:
        app.configure(dependencies, settings)
        return app

    monkeypatch.setattr(cli, "RibelleApp", create_app)


def test_main_opens_textual_with_search_arguments_and_accessibility_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = FakeTextualApp()
    install_fake_components(monkeypatch, app)

    return_code = cli.main(
        [
            "--artist",
            "Artist",
            "--album",
            "Album",
            "--device",
            "/dev/sr1",
            "--output",
            str(tmp_path),
            "--high-contrast",
            "--reduced-motion",
        ]
    )

    assert return_code == 0
    assert app.started
    assert app.settings == {
        "initial_artist": "Artist",
        "initial_album": "Album",
        "initial_device": "/dev/sr1",
        "output_directory": tmp_path,
        "high_contrast": True,
        "reduced_motion": True,
    }


def test_main_allows_entering_search_terms_inside_the_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FakeTextualApp()
    install_fake_components(monkeypatch, app)

    return_code = cli.main([])

    assert return_code == 0
    assert app.started
    assert app.settings["initial_artist"] is None
    assert app.settings["initial_album"] is None
    assert app.settings["initial_device"] is None
    assert app.settings["output_directory"] == Path("~/Music")


def test_main_returns_the_textual_application_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FakeTextualApp()
    app.return_code = 1
    install_fake_components(monkeypatch, app)

    assert cli.main([]) == 1


def test_main_keeps_a_standard_help_screen() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["--help"])

    assert error.value.code == 0
