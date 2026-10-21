import pytest

from ribelle.domain.album_metadata import AlbumMetadata, TrackMetadata


def test_album_metadata_exposes_tracks_in_disc_order() -> None:
    album = AlbumMetadata(
        artist="The Example Band",
        title="First Album",
        year="2024",
        tracks=(
            TrackMetadata(number=2, title="Second Song"),
            TrackMetadata(number=1, title="First Song"),
        ),
    )

    assert [track.number for track in album.tracks] == [1, 2]


def test_album_metadata_rejects_an_album_without_tracks() -> None:
    with pytest.raises(ValueError, match="at least one track"):
        AlbumMetadata(artist="Artist", title="Album", year="2024", tracks=())


def test_track_metadata_rejects_track_numbers_below_one() -> None:
    with pytest.raises(ValueError, match="positive"):
        TrackMetadata(number=0, title="Song")


def test_track_metadata_rejects_disc_numbers_below_one() -> None:
    with pytest.raises(ValueError, match="Disc number must be positive"):
        TrackMetadata(number=1, title="Song", disc_number=0)


def test_track_metadata_rejects_blank_titles() -> None:
    with pytest.raises(ValueError, match="title"):
        TrackMetadata(number=1, title="  ")


@pytest.mark.parametrize(
    ("artist", "title", "message"),
    [("  ", "Album", "artist"), ("Artist", "  ", "title")],
)
def test_album_metadata_rejects_blank_artist_or_title(
    artist: str,
    title: str,
    message: str,
) -> None:
    track = TrackMetadata(1, "Song")

    with pytest.raises(ValueError, match=message):
        AlbumMetadata(artist=artist, title=title, year=None, tracks=(track,))


def test_album_metadata_rejects_duplicate_track_numbers() -> None:
    tracks = (TrackMetadata(1, "First"), TrackMetadata(1, "Duplicate"))

    with pytest.raises(ValueError, match="unique within each disc"):
        AlbumMetadata(artist="Artist", title="Album", year=None, tracks=tracks)


def test_album_metadata_orders_tracks_by_disc_then_track_number() -> None:
    tracks = (
        TrackMetadata(1, "Second disc", disc_number=2),
        TrackMetadata(2, "Second track", disc_number=1),
        TrackMetadata(1, "First track", disc_number=1),
    )

    album = AlbumMetadata(artist="Artist", title="Album", year=None, tracks=tracks)

    assert [(track.disc_number, track.number) for track in album.tracks] == [(1, 1), (1, 2), (2, 1)]
