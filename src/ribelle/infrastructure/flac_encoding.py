"""Lossless FLAC encoding through the flac command-line tool."""

from pathlib import Path
from subprocess import run


class FlacEncoderAdapter:
    """Encode a WAV file as FLAC without modifying the source waveform."""

    def encode(self, wave_path: Path, flac_path: Path) -> None:
        run(
            [
                "flac",
                "--silent",
                "--best",
                "--output-name",
                str(flac_path),
                str(wave_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
