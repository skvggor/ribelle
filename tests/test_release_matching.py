from ribelle.application.release_matching import find_matching_discs
from ribelle.domain.album_metadata import AlbumMetadata, TrackMetadata


def test_matches_all_album_discs_with_the_physical_track_count() -> None:
    first_album = AlbumMetadata(
        artist="Artist",
        title="Double Album",
        year="2024",
        tracks=(
            TrackMetadata(1, "One"),
            TrackMetadata(2, "Two"),
            TrackMetadata(1, "Three", disc_number=2),
        ),
    )
    second_album = AlbumMetadata(
        artist="Artist",
        title="Alternate Edition",
        year="2023",
        tracks=(TrackMetadata(1, "Only track"),),
    )

    matches = find_matching_discs((first_album, second_album), track_count=1)

    assert matches == ((first_album, 2), (second_album, 1))


def test_returns_no_discs_when_track_counts_do_not_match() -> None:
    album = AlbumMetadata(
        artist="Artist",
        title="Album",
        year=None,
        tracks=(TrackMetadata(1, "One"), TrackMetadata(2, "Two")),
    )

    assert find_matching_discs((album,), track_count=3) == ()
