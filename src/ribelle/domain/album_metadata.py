"""Validated album and track metadata used throughout the application."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrackMetadata:
    """Metadata describing one track on an audio CD."""

    number: int
    title: str
    disc_number: int = 1

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError("Track number must be positive")
        if self.disc_number < 1:
            raise ValueError("Disc number must be positive")
        if not self.title.strip():
            raise ValueError("Track title cannot be blank")


@dataclass(frozen=True, slots=True)
class AlbumMetadata:
    """Metadata shared by tracks on one album."""

    artist: str
    title: str
    year: str | None
    tracks: tuple[TrackMetadata, ...]

    def __post_init__(self) -> None:
        if not self.artist.strip():
            raise ValueError("Album artist cannot be blank")
        if not self.title.strip():
            raise ValueError("Album title cannot be blank")
        if not self.tracks:
            raise ValueError("Album must contain at least one track")
        track_numbers = [(track.disc_number, track.number) for track in self.tracks]
        if len(track_numbers) != len(set(track_numbers)):
            raise ValueError("Track numbers must be unique within each disc")
        ordered_tracks = tuple(
            sorted(self.tracks, key=lambda track: (track.disc_number, track.number))
        )
        object.__setattr__(self, "tracks", ordered_tracks)
