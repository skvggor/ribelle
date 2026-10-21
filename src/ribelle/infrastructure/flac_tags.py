"""FLAC metadata writing through Mutagen."""

from pathlib import Path

from mutagen.flac import FLAC

from ribelle.domain.album_metadata import AlbumMetadata, TrackMetadata


class MutagenTagWriter:
    """Write Vorbis comments to FLAC files."""

    def tag(self, file_path: Path, album: AlbumMetadata, track: TrackMetadata) -> None:
        audio_file = FLAC(file_path)
        if audio_file is None:
            raise ValueError(f"Could not read FLAC file: {file_path}")

        tracks_on_disc = tuple(
            album_track
            for album_track in album.tracks
            if album_track.disc_number == track.disc_number
        )
        disc_count = len({album_track.disc_number for album_track in album.tracks})
        audio_file["ARTIST"] = album.artist
        audio_file["ALBUMARTIST"] = album.artist
        audio_file["ALBUM"] = album.title
        audio_file["TITLE"] = track.title
        audio_file["TRACKNUMBER"] = str(track.number)
        audio_file["TRACKTOTAL"] = str(len(tracks_on_disc))
        audio_file["DISCNUMBER"] = str(track.disc_number)
        audio_file["DISCTOTAL"] = str(disc_count)
        if album.year:
            audio_file["DATE"] = album.year
        audio_file.save()
