"""Match release discs to the audio disc currently in the drive."""

from ribelle.domain.album_metadata import AlbumMetadata


def find_matching_discs(
    albums: tuple[AlbumMetadata, ...],
    track_count: int,
) -> tuple[tuple[AlbumMetadata, int], ...]:
    """Return release discs whose track count matches the inserted CD."""
    matching_discs: list[tuple[AlbumMetadata, int]] = []
    for album in albums:
        disc_numbers = sorted({track.disc_number for track in album.tracks})
        for disc_number in disc_numbers:
            disc_track_count = sum(track.disc_number == disc_number for track in album.tracks)
            if disc_track_count == track_count:
                matching_discs.append((album, disc_number))
    return tuple(matching_discs)
