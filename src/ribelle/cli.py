"""Command-line entry point for the Ribelle Textual application."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from ribelle.application.rip_album import AlbumRipper
from ribelle.infrastructure.audio_cd import CdParanoiaDrive
from ribelle.infrastructure.flac_encoding import FlacEncoderAdapter
from ribelle.infrastructure.flac_tags import MutagenTagWriter
from ribelle.infrastructure.musicbrainz_catalog import MusicBrainzCatalog
from ribelle.presentation.ribelle_app import RibelleApp


def main(arguments: Sequence[str] | None = None) -> int:
    """Launch Ribelle with optional search terms and accessibility preferences."""
    argument_parser = argparse.ArgumentParser(
        description="Back up audio CDs as lossless FLAC files with Ribelle"
    )
    argument_parser.add_argument("--artist", help="Album artist to search for")
    argument_parser.add_argument("--album", help="Album title to search for")
    argument_parser.add_argument("--device", help="Preselect a CD drive such as /dev/sr1")
    argument_parser.add_argument(
        "--output",
        type=Path,
        default=Path("~/Music"),
        help="Destination for artist and album folders",
    )
    argument_parser.add_argument(
        "--high-contrast",
        action="store_true",
        help="Use the terminal's high-contrast ANSI palette",
    )
    argument_parser.add_argument(
        "--reduced-motion",
        action="store_true",
        help="Disable animated loading indicators",
    )
    options = argument_parser.parse_args(arguments)

    disc_reader = CdParanoiaDrive()
    app = RibelleApp(
        disc_reader,
        MusicBrainzCatalog(),
        AlbumRipper(disc_reader, FlacEncoderAdapter(), MutagenTagWriter()),
        initial_artist=options.artist,
        initial_album=options.album,
        initial_device=options.device,
        output_directory=options.output,
        high_contrast=options.high_contrast,
        reduced_motion=options.reduced_motion,
    )
    app.run()
    return app.return_code or 0
