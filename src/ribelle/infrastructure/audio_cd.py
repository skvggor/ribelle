"""Audio CD extraction through cdparanoia."""

import re
from pathlib import Path
from subprocess import run

TRACK_LINE = re.compile(r"^\s*(\d+)\.\s+\d+\s+\[", re.MULTILINE)


class CdParanoiaDrive:
    """Read a CD's track list and extract individual tracks."""

    def discover_devices(self, device_directory: Path = Path("/dev")) -> tuple[str, ...]:
        return tuple(str(device) for device in sorted(device_directory.glob("sr*")))

    def track_count(self, device: str) -> int:
        result = run(
            ["cdparanoia", "-Q", "-d", device],
            check=True,
            capture_output=True,
            text=True,
        )
        table_of_contents = f"{result.stdout}\n{result.stderr}"
        return len(TRACK_LINE.findall(table_of_contents))

    def rip_track(self, device: str, track_number: int, wave_path: Path) -> None:
        run(
            ["cdparanoia", "-d", device, str(track_number), str(wave_path)],
            check=True,
            capture_output=True,
            text=True,
        )
