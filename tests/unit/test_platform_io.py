import errno
import os
import stat
from types import SimpleNamespace

import pytest

from camel_harness.agent import platform_io
from camel_harness.agent.resources import memory


def test_open_regular_checks_type_and_returns_owned_descriptor(tmp_path):
    path = tmp_path / "sample"
    descriptor = platform_io.open_regular(path, writable=True, create=True)
    try:
        os.write(descriptor, "中文".encode())
    finally:
        os.close(descriptor)
    assert path.read_text("utf-8") == "中文"
    with pytest.raises((ValueError, OSError)):
        platform_io.open_regular(tmp_path)
    with pytest.raises(FileNotFoundError):
        platform_io.open_regular(tmp_path / "missing")


def test_is_link_rejects_windows_reparse_points(monkeypatch, tmp_path):
    monkeypatch.setattr(
        type(tmp_path),
        "lstat",
        lambda _: SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400),
    )
    assert platform_io.is_link(tmp_path)


def test_windows_directory_sync_does_not_open_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_io, "IS_WINDOWS", True)

    def forbidden(*args):
        pytest.fail("Windows 不应使用 CRT 打开目录进行 fsync")

    monkeypatch.setattr(os, "open", forbidden)
    platform_io.sync_directory(tmp_path)


@pytest.mark.parametrize("attributes", [0, 0x400, 0x10])
@pytest.mark.parametrize("legacy_pywin32", [False, True])
def test_windows_safe_open_checks_handle_and_transfers_ownership(
    monkeypatch, tmp_path, attributes, legacy_pywin32
):
    class Handle:
        closed = False
        detached = False

        def __int__(self):
            return 123

        def Close(self):
            self.closed = True

        def Detach(self):
            self.detached = True

    handle = Handle()
    calls = []
    constants = SimpleNamespace(
        GENERIC_READ=1,
        GENERIC_WRITE=2,
        FILE_SHARE_READ=1,
        FILE_SHARE_WRITE=2,
        FILE_SHARE_DELETE=4,
        OPEN_ALWAYS=4,
        OPEN_EXISTING=3,
        FILE_FLAG_OPEN_REPARSE_POINT=0x200000,
        FILE_FLAG_BACKUP_SEMANTICS=0x2000000,
        FILE_ATTRIBUTE_REPARSE_POINT=0x400,
        FILE_ATTRIBUTE_DIRECTORY=0x10,
    )
    if legacy_pywin32:
        # 旧版 pywin32 未导出 FILE_FLAG_OPEN_REPARSE_POINT 绑定。
        delattr(constants, "FILE_FLAG_OPEN_REPARSE_POINT")
    modules = {
        "win32con": constants,
        "win32file": SimpleNamespace(
            CreateFile=lambda *args: calls.append(args) or handle,
            GetFileInformationByHandle=lambda _: (attributes,),
        ),
        "pywintypes": SimpleNamespace(error=OSError),
        "msvcrt": SimpleNamespace(open_osfhandle=lambda *args: 42),
    }
    monkeypatch.setattr(platform_io, "import_module", modules.__getitem__)
    if attributes:
        with pytest.raises(ValueError, match="普通文件"):
            platform_io._open_windows(tmp_path, writable=True, create=True)
        assert handle.closed and not handle.detached
    else:
        assert platform_io._open_windows(tmp_path, writable=True, create=True) == 42
        assert handle.detached and not handle.closed
    assert calls[0][2] == 7
    assert calls[0][4] == constants.OPEN_ALWAYS
    assert calls[0][5] & 0x200000  # FILE_FLAG_OPEN_REPARSE_POINT，旧版 pywin32 缺绑定时按文档值补齐


def test_windows_safe_open_preserves_missing_file_semantics(monkeypatch, tmp_path):
    class WindowsError(Exception):
        winerror = 2

    def missing(*args):
        raise WindowsError

    modules = {
        "win32con": SimpleNamespace(
            GENERIC_READ=1,
            FILE_SHARE_READ=1,
            FILE_SHARE_WRITE=2,
            FILE_SHARE_DELETE=4,
            OPEN_EXISTING=3,
            FILE_FLAG_OPEN_REPARSE_POINT=1,
            FILE_FLAG_BACKUP_SEMANTICS=2,
        ),
        "win32file": SimpleNamespace(CreateFile=missing),
        "pywintypes": SimpleNamespace(error=WindowsError),
        "msvcrt": SimpleNamespace(),
    }
    monkeypatch.setattr(platform_io, "import_module", modules.__getitem__)
    with pytest.raises(FileNotFoundError) as error:
        platform_io._open_windows(tmp_path, writable=False, create=False)
    assert error.value.errno == errno.ENOENT


async def test_memory_uses_fixed_filenames_and_rejects_file_links(tmp_path):
    from camel_harness.agent.config import MemorySettings

    memory_store = memory.MemoryStore(tmp_path, MemorySettings(), user_root=tmp_path / "user")
    await memory_store.add("memory", "内容")
    path = tmp_path / ".camel" / "memory" / "MEMORY.md"
    assert path.exists()

    path.unlink()
    outside = tmp_path / "outside.md"
    outside.write_text("外部内容", encoding="utf-8")
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="符号链接"):
        await memory_store.snapshot("memory")
