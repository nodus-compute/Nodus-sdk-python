"""Observable archive contents, capacity and local snapshot ownership."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

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


@pytest.mark.parametrize("nested", [False, True])
def test_windows_cached_directory_identity_does_not_reject_unchanged_project(tmp_path, monkeypatch, nested):
    project = tmp_path / "project"
    project.mkdir()
    parent = project / "data" if nested else project
    parent.mkdir(exist_ok=True)
    (parent / "train.py").write_bytes(b"print(42)\n")
    original = archive.os.scandir

    class CachedStat:
        # Windows DirEntry.stat caches these as zero, unlike os.stat/fstat.
        st_dev = st_ino = st_nlink = 0

        def __init__(self, info):
            self.info = info

        def __getattr__(self, name):
            return getattr(self.info, name)

    class WindowsDirectory:
        def __init__(self, path):
            self.iterator = original(path)

        def __enter__(self):
            self.iterator.__enter__()
            return self

        def __exit__(self, *args):
            return self.iterator.__exit__(*args)

        def __iter__(self):
            for child in self.iterator:
                yield SimpleNamespace(
                    name=child.name,
                    stat=lambda *, follow_symlinks=True, child=child: CachedStat(
                        child.stat(follow_symlinks=follow_symlinks)
                    ),
                )

    def windows_scandir(path):
        if not isinstance(path, int) and Path(path).is_relative_to(project):
            return WindowsDirectory(path)
        return original(path)

    monkeypatch.setattr(archive.os, "scandir", windows_scandir)
    with archive.build_workspace_archive(project) as built:
        with tarfile.open(built.path) as contents:
            expected = "data/train.py" if nested else "train.py"
            assert contents.extractfile(expected).read() == b"print(42)\n"
        assert built.manifest["inventory"]["payload_bytes"] == 10
        assert built.manifest["inventory"]["entries"] == (2 if nested else 1)


def test_replaced_identical_file_cannot_publish_a_snapshot(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "train.py"
    target.write_bytes(b"original")
    before = target.stat()
    replacement = tmp_path / "replacement.py"
    replacement.write_bytes(target.read_bytes())
    replacement.chmod(before.st_mode)
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    original = archive._header

    def replace_after_scan(*args, **kwargs):
        replacement.replace(target)
        return original(*args, **kwargs)

    monkeypatch.setattr(archive, "_header", replace_after_scan)
    with pytest.raises(ValidationError, match="Project changed while preparing upload"):
        with archive.build_workspace_archive(project):
            pytest.fail("replacement file was accepted as the original snapshot")


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
@pytest.mark.parametrize("directory", [False, True])
def test_upload_never_reads_symlink_target(tmp_path, directory):
    secret = tmp_path / "outside"
    if directory:
        secret.mkdir()
        (secret / "contents").write_bytes(b"must not upload")
    else:
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
    def broken_scandir(root):
        raise PermissionError("synthetic unreadable directory")

    monkeypatch.setattr(archive.os, "scandir", broken_scandir)
    with pytest.raises(ValidationError, match="every project directory"):
        with archive.build_workspace_archive(tmp_path):
            pytest.fail("partial project admitted")


def test_directory_read_failure_after_an_entry_rejects_partial_project(tmp_path, monkeypatch):
    (tmp_path / "train.py").write_bytes(b"payload")
    original = archive.os.scandir
    closed = False

    class FailingDirectory:
        def __enter__(self):
            self.iterator = original(tmp_path).__enter__()
            self.read = False
            return self

        def __exit__(self, *args):
            nonlocal closed
            closed = True
            return self.iterator.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            if self.read:
                raise OSError("directory read interrupted")
            self.read = True
            return next(self.iterator)

    monkeypatch.setattr(archive.os, "scandir", lambda _: FailingDirectory())
    with pytest.raises(ValidationError, match="every project directory"):
        with archive.build_workspace_archive(tmp_path):
            pytest.fail("partly read project admitted")
    assert closed


def test_directory_changes_after_scan_cannot_omit_new_files(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    child = project / "data"
    child.mkdir()
    (child / "existing").write_bytes(b"first")
    original = archive._header

    def change_after_scan(*args, **kwargs):
        (child / "added-during-snapshot").write_bytes(b"must not be silently omitted")
        return original(*args, **kwargs)

    monkeypatch.setattr(archive, "_header", change_after_scan)
    with pytest.raises(ValidationError, match="directory changed"):
        with archive.build_workspace_archive(project):
            pytest.fail("directory changes were silently omitted")


@pytest.mark.parametrize("change", ["add", "remove", "rename"])
def test_directory_membership_changes_are_rejected_with_unchanged_metadata(tmp_path, monkeypatch, change):
    project = tmp_path / "project"
    project.mkdir()
    child = project / "data"
    child.mkdir()
    existing = child / "existing"
    existing.write_bytes(b"first")
    (project / "z-final").write_bytes(b"last")
    initial = child.stat()
    original_lstat = Path.lstat
    original_header = archive._header

    def windows_directory_lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == child:
            # Windows directories have zero size, independent of membership.
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                                   st_mode=info.st_mode, st_size=0,
                                   st_mtime_ns=info.st_mtime_ns)
        return info

    def change_after_child_was_copied(name, *args, **kwargs):
        if name == "z-final":
            if change == "add":
                (child / "new").write_bytes(b"must not be omitted")
            elif change == "remove":
                existing.unlink()
            else:
                existing.rename(child / "renamed")
            os.utime(child, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            assert child.stat().st_mtime_ns == initial.st_mtime_ns
        return original_header(name, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", windows_directory_lstat)
    monkeypatch.setattr(archive, "_header", change_after_child_was_copied)
    with pytest.raises(ValidationError, match="directory changed"):
        with archive.build_workspace_archive(project):
            pytest.fail("changed directory membership was published")


def test_oversized_directory_stops_enumerating_at_the_entry_bound(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    for index in range(100):
        (project / f"file-{index}").write_bytes(b"x")
    original = archive.os.scandir
    enumerated = 0
    closed = False

    class TrackedDirectory:
        def __init__(self, path):
            self.iterator = original(path)

        def __enter__(self):
            self.iterator.__enter__()
            return self

        def __exit__(self, *args):
            nonlocal closed
            closed = True
            return self.iterator.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal enumerated
            child = next(self.iterator)
            enumerated += 1
            return child

    monkeypatch.setattr(archive.os, "scandir", TrackedDirectory)
    monkeypatch.setattr(archive, "_ENTRY_LIMIT", 2)
    with pytest.raises(ValidationError, match="file count"):
        with archive.build_workspace_archive(project):
            pytest.fail("oversized project admitted")
    assert enumerated == 3, "oversized directory was materialized before its limit was enforced"
    assert closed, "directory iterator leaked after rejecting oversized project"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO replacement is a POSIX file race")
def test_replaced_fifo_is_rejected_without_waiting_for_a_writer(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "train.py").write_bytes(b"original")
    # An isolated process makes a blocking regression fail within a fixed bound.
    # Load the same artifact as this test, including during installed-wheel tests.
    script = r'''
import os, pathlib, sys, types
package = types.ModuleType("nodus")
package.__path__ = [sys.argv[1]]
sys.modules["nodus"] = package
from nodus import _workspace_archive as archive
from nodus.errors import ValidationError
root = pathlib.Path(sys.argv[2])
target = root / "train.py"
original = archive._header
def replace_with_fifo(*args, **kwargs):
    result = original(*args, **kwargs)
    target.unlink()
    os.mkfifo(target)
    return result
archive._header = replace_with_fifo
try:
    with archive.build_workspace_archive(root):
        raise AssertionError("replacement FIFO was admitted")
except ValidationError as error:
    assert "changed" in str(error), str(error)
'''
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(Path(archive.__file__).parent), str(project)],
            capture_output=True, text=True, timeout=3,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("archive preparation blocked opening a replacement FIFO")
    assert result.returncode == 0, result.stdout + result.stderr
