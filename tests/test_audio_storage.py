from io import BytesIO, StringIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.storage.local import AudioStorageError


@pytest.mark.parametrize("extension", ["wav", "MP3", "webm", "m4a"])
def test_save_read_delete(storage, extension):
    stream = BytesIO(b"example audio bytes")
    key = storage.save(stream, filename=f"user-recording.{extension}")
    assert UUID(Path(key).stem).version == 4
    assert key.endswith(f".{extension.lower()}")
    assert "user-recording" not in key
    assert storage.get_path(key).read_bytes() == b"example audio bytes"
    assert not stream.closed
    assert list(storage.directory.iterdir()) == [storage.get_path(key)]
    storage.delete(key)
    storage.delete(key)
    assert not storage.get_path(key).exists()


@pytest.mark.parametrize("content", [b"", b"x" * (1024 * 1024 + 1)])
def test_invalid_size_cleans_up(storage, content):
    with pytest.raises(AudioStorageError):
        storage.save(BytesIO(content), filename="audio.wav")
    assert not list(storage.directory.iterdir())


def test_exact_limit_and_bounded_reads(storage):
    class BoundedStream(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= storage.CHUNK_SIZE
            return super().read(size)

    content = b"x" * storage.max_bytes
    key = storage.save(BoundedStream(content), filename="audio.wav")
    assert storage.get_path(key).read_bytes() == content


@pytest.mark.parametrize(
    "filename",
    ["a.txt", "a", "/tmp/a.wav", "../a.wav", "a/../../x.mp3", "C:\\a.wav", "a\x00.wav"],
)
def test_bad_filename(storage, filename):
    with pytest.raises(AudioStorageError):
        storage.save(BytesIO(b"audio"), filename=filename)
    assert not storage.directory.exists()


@pytest.mark.parametrize("operation", ["get_path", "delete"])
@pytest.mark.parametrize(
    "key", ["../outside.wav", "/tmp/a.mp3", "x/../a.wav", "C:\\a.wav", "a.wav"]
)
def test_path_traversal_rejected(storage, operation, key):
    with pytest.raises(AudioStorageError):
        getattr(storage, operation)(key)


def test_symlink_cannot_escape_storage(storage, tmp_path):
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"must stay")
    storage.directory.mkdir()
    key = f"{uuid4()}.wav"
    (storage.directory / key).symlink_to(outside)
    with pytest.raises(AudioStorageError):
        storage.get_path(key)
    with pytest.raises(AudioStorageError):
        storage.delete(key)
    assert outside.read_bytes() == b"must stay"


def test_nonbinary_stream_rejected(storage):
    with pytest.raises(AudioStorageError):
        storage.save(StringIO("audio"), filename="audio.wav")
    assert not list(storage.directory.iterdir())


def test_read_error_cleans_partial_file(storage):
    class BrokenStream:
        calls = 0

        def read(self, size):
            self.calls += 1
            if self.calls == 1:
                return b"first chunk"
            raise OSError("sensitive stream details")

    with pytest.raises(AudioStorageError, match="Unable to save audio"):
        storage.save(BrokenStream(), filename="audio.wav")
    assert not list(storage.directory.iterdir())


def test_write_error_cleans_partial_file(storage, monkeypatch):
    original_open = Path.open

    class BrokenDestination:
        def __enter__(self):
            return self

        def write(self, data):
            self.file.write(data[:1])
            raise OSError("disk full")

        def __exit__(self, *_):
            self.file.close()

    def broken_open(path, mode="r", *args, **kwargs):
        file = original_open(path, mode, *args, **kwargs)
        if mode == "xb":
            destination = BrokenDestination()
            destination.file = file
            return destination
        return file

    monkeypatch.setattr(Path, "open", broken_open)
    with pytest.raises(AudioStorageError):
        storage.save(BytesIO(b"audio"), filename="audio.wav")
    assert not list(storage.directory.iterdir())


def test_publish_error_cleans_temp(storage, monkeypatch):
    def cannot_rename(*_):
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "replace", cannot_rename)
    with pytest.raises(AudioStorageError):
        storage.save(BytesIO(b"audio"), filename="audio.wav")
    assert not list(storage.directory.iterdir())
