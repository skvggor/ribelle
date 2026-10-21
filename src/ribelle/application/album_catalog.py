"""Port for finding album metadata."""

from typing import Protocol, runtime_checkable

from ribelle.domain.album_metadata import AlbumMetadata


@runtime_checkable
class AlbumCatalog(Protocol):
    """Provides album metadata from an external catalog."""

    def search(self, artist: str, title: str) -> tuple[AlbumMetadata, ...]:
        """Find matching album releases."""
