"""Observable archive contents, capacity and local snapshot ownership."""
import hashlib
import os
from pathlib import Path
import tarfile

import pytest

from nodus import _workspace_archive as archive
from nodus.errors import ValidationError


def test_project_archive_preserves_files_modes_and_cleans_temporary_disk(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "data").mkdir()
    (project / "data" / "café.txt").write_bytes(b"training data")
    program = project / "train.py"
    program.write_bytes(b"print(42)\n")
    program.chmod(0o755)
    with archive.build_workspace_archive(project) as built:
        temporary = built.path
        raw = temporary.read_bytes()
        assert built.manifest["inventory"]["payload_bytes"] == 23
        assert built.manifest["inventory"]["entries"] == 3
        assert built.manifest["archive_bytes"] == len(raw)
        assert built.manifest["archive_sha256"] == hashlib.sha256(raw).hexdigest()
        with tarfile.open(temporary) as contents:
            assert contents.getnames() == ["data", "data/café.txt", "train.py"]
            assert contents.extractfile("data/café.txt").read() == b"training data"
            assert contents.extractfile("train.py").read() == b"print(42)\n"
            if os.name != "nt":
                assert contents.getmember("train.py").mode == 0o755
        assert raw[-1024:] == bytes(1024)
    assert not temporary.exists()


def test_archive_is_stable_across_mtime_and_directory_creation_order(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "z.txt").write_bytes(b"z")
    (project / "a.txt").write_bytes(b"a")
    with archive.build_workspace_archive(project) as first:
        initial = first.path.read_bytes(), first.manifest
    os.utime(project / "z.txt", (50, 50))
    with archive.build_workspace_archive(project) as second:
        assert (second.path.read_bytes(), second.manifest) == initial


def test_chunked_file_and_record_termination_have_verified_offsets(tmp_path):
    # A real sparse file crosses the production 64 MiB boundary with bounded RAM.
    project = tmp_path / "project"
    project.mkdir()
    large = project / "weights.bin"
    with large.open("wb") as stream:
        stream.truncate((64 << 20) + 17)
    with archive.build_workspace_archive(project) as built:
        segments = built.manifest["segments"]
        assert [segment["bytes"] for segment in segments] == [64 << 20, 1024, 1024]
        assert [segment["offset"] for segment in segments] == [0, 64 << 20, (64 << 20) + 1024]
        with built.path.open("rb") as stream:
            for segment in segments:
                digest = hashlib.sha256()
                remaining = segment["bytes"]
                while remaining:
                    chunk = stream.read(min(1 << 20, remaining))
                    assert chunk
                    digest.update(chunk)
                    remaining -= len(chunk)
                assert digest.hexdigest() == segment["sha256"]


def test_empty_or_oversized_project_is_rejected_before_upload(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValidationError, match="empty"):
        with archive.build_workspace_archive(project):
            pytest.fail("empty project admitted")
    (project / "data").write_bytes(b"four")
    monkeypatch.setattr(archive, "_PAYLOAD_LIMIT", 3)
    with pytest.raises(ValidationError, match="capacity"):
        with archive.build_workspace_archive(project):
            pytest.fail("oversized project admitted")


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation requires explicit privileges")
def test_upload_never_reads_symlink_target(tmp_path):
    secret = tmp_path / "outside"
    secret.write_bytes(b"must not upload")
    project = tmp_path / "project"
    project.mkdir()
    (project / "linked").symlink_to(secret)
    with pytest.raises(ValidationError, match="regular files"):
        with archive.build_workspace_archive(project):
            pytest.fail("outside symlink admitted")


def test_changed_file_cannot_publish_a_mixed_snapshot(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "train.py"
    target.write_bytes(b"old")
    original = archive._header

    def change_after_scan(*args, **kwargs):
        target.write_bytes(b"new contents")
        return original(*args, **kwargs)

    monkeypatch.setattr(archive, "_header", change_after_scan)
    with pytest.raises(ValidationError, match="changed"):
        with archive.build_workspace_archive(project):
            pytest.fail("changing files admitted")


def test_unreadable_directory_is_not_silently_omitted(tmp_path, monkeypatch):
    def broken_walk(root, *, followlinks, onerror):
        onerror(PermissionError("synthetic unreadable directory"))
        return iter(())

    monkeypatch.setattr(archive.os, "walk", broken_walk)
    with pytest.raises(ValidationError, match="every project directory"):
        with archive.build_workspace_archive(tmp_path):
            pytest.fail("partial project admitted")
