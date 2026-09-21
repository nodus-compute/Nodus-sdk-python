"""Select a release candidate without changing the declared package version."""
from email.parser import BytesParser
from pathlib import Path
import zipfile


def package_source(version: str, wheel: Path | None = None) -> str:
    if wheel is None:
        return f"nodus-compute[mcp]=={version}"
    wheel = wheel.resolve(strict=True)
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise ValueError("Expected exactly one wheel metadata file")
        metadata = BytesParser().parsebytes(archive.read(names[0]))
    if metadata["Name"].replace("_", "-") != "nodus-compute" or metadata["Version"] != version:
        raise ValueError("The wheel must match the plugin's pinned package and version")
    return f"nodus-compute[mcp] @ {wheel.as_uri()}"
