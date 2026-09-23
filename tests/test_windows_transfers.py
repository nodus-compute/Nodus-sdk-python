"""Windows transfers preserve bytes without traversing redirected paths."""

import os
import subprocess

import pytest

from nodus import ValidationError
from nodus._windows_paths import normalized_path, validate_name, validate_names


@pytest.mark.parametrize("name", ["CON", "nul.txt", "COM1.log", "LPT²", "CONIN$", "file:stream", "trailing.", "trailing ", "..", "a/b", "a\\b", "bad\x00name", "bad?name", ""])
def test_windows_names_cannot_alias_devices_streams_or_other_paths(name):
    with pytest.raises(ValidationError):
        validate_name(name)


@pytest.mark.parametrize("names", [["Report.txt", "report.TXT"], ["I.txt", "ı.txt"]])
def test_windows_case_collisions_cannot_silently_replace_another_download(names):
    with pytest.raises(ValidationError, match="case"):
        validate_names(names)


def test_windows_regular_unicode_names_preserve_their_exact_spelling():
    names = ["report.txt", "résultats 日本語.txt", "space in name", ".env.example"]
    assert validate_names(names) == names


@pytest.mark.parametrize("path", [r"\\server\share\file", r"\\?\C:\file", r"\\.\NUL", "C:project", "x:stream", "C:/safe/CON", "C:/trailing.\\file"])
def test_windows_ambiguous_or_network_paths_are_refused_before_filesystem_access(path):
    with pytest.raises(ValidationError):
        normalized_path(path)


windows_only = pytest.mark.skipif(os.name != "nt", reason="Requires native Windows NTFS handles")


def junction(link, target):
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True, timeout=30)
    assert made.returncode == 0, made.stdout + made.stderr


@windows_only
def test_windows_directory_handle_rejects_a_junction_without_reading_target(tmp_path):
    from nodus._local_files import open_directory
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret.txt").write_bytes(b"private")
    junction(tmp_path / "redirect", private)
    with pytest.raises(ValidationError, match="reparse"):
        with open_directory(tmp_path / "redirect"):
            pytest.fail("junction accepted")


@windows_only
def test_windows_held_directory_cannot_be_swapped_during_transfer(tmp_path):
    from nodus._local_files import open_directory
    selected = tmp_path / "selected"
    selected.mkdir()
    with open_directory(selected):
        with pytest.raises(PermissionError):
            selected.rename(tmp_path / "moved")
    selected.rename(tmp_path / "moved")


@windows_only
def test_windows_source_is_held_against_concurrent_writers(tmp_path):
    from nodus._local_files import open_directory
    source = tmp_path / "source.bin"
    source.write_bytes(b"exact source bytes")
    with open_directory(tmp_path) as directory, directory.open_read(source.name) as opened:
        with pytest.raises(PermissionError):
            source.write_bytes(b"changed")
        assert opened.read() == b"exact source bytes"
    source.write_bytes(b"released")


@windows_only
def test_windows_archive_excludes_case_variants_of_credentials(tmp_path):
    import tarfile
    from nodus._projects import archive_project
    (tmp_path / ".ENV").write_bytes(b"secret")
    (tmp_path / "main.py").write_bytes(b"print('hello')\n")
    with archive_project(tmp_path, 100000) as archive_path, tarfile.open(archive_path) as archive:
        assert archive.getnames() == ["main.py"]
        assert archive.extractfile("main.py").read() == b"print('hello')\n"


@windows_only
def test_windows_download_rejects_destination_junction_and_releases_handles(tmp_path):
    from nodus._local_files import open_directory
    destination, private = tmp_path / "destination", tmp_path / "private"
    destination.mkdir()
    private.mkdir()
    junction(destination / "result", private)
    with open_directory(destination) as directory:
        with pytest.raises(ValidationError, match="reparse"):
            with directory.stage() as staged:
                staged.write(b"verified bytes")
                staged.commit("result")
    assert list(private.iterdir()) == []
    assert [path.name for path in destination.iterdir()] == ["result"]
    destination.rename(tmp_path / "released")


@windows_only
@pytest.mark.parametrize("name", ["result.bin", "x", "😀.txt"])
def test_windows_download_atomically_publishes_verified_bytes_and_cleans_failed_files(tmp_path, name, monkeypatch):
    from nodus._local_files import open_directory
    destination = tmp_path / "destination"
    destination.mkdir()
    output = destination / name
    output.write_bytes(b"original")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    sentinel = unrelated / name
    sentinel.write_bytes(b"unrelated")
    monkeypatch.chdir(unrelated)
    for _ in range(3):
        with open_directory(destination) as directory:
            with pytest.raises(RuntimeError):
                with directory.stage() as staged:
                    staged.write(b"incomplete")
                    raise RuntimeError("interrupted")
            assert output.read_bytes() == b"original"
            assert list(destination.iterdir()) == [output]
    with open_directory(destination) as directory, directory.stage() as staged:
        staged.write(b"verified result")
        assert output.read_bytes() == b"original"
        staged.commit(output.name)
    assert output.read_bytes() == b"verified result"
    assert list(destination.iterdir()) == [output]
    assert sentinel.read_bytes() == b"unrelated"
    assert list(unrelated.iterdir()) == [sentinel]


@windows_only
def test_windows_long_unicode_paths_and_nested_creation_preserve_bytes(tmp_path):
    from nodus._local_files import open_directory
    directory_path = tmp_path / ("directory" * 12) / ("日本語" * 30) / ("nested" * 15)
    name = "résultats.txt"
    with open_directory(directory_path, create=True) as directory, directory.stage() as staged:
        staged.write(b"exact bytes\x00\xff")
        staged.commit(name)
    with open_directory(directory_path) as directory, directory.open_read(name) as source:
        assert source.read() == b"exact bytes\x00\xff"
