"""只封装实际存在差异的文件操作，不改变上层存储协议。"""

from __future__ import annotations

import errno
import os
import stat
import sys
from importlib import import_module
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


def is_link(path: Path) -> bool:
    """Windows junction 也是重解析点，不能仅检查符号链接。"""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def open_regular(path: Path, *, writable: bool = False, create: bool = False) -> int:
    """打开普通文件且不跟随最终路径链接；调用者拥有返回的描述符。"""
    if IS_WINDOWS:
        descriptor = _open_windows(path, writable=writable, create=create)
    else:
        flags = os.O_RDWR if writable else os.O_RDONLY
        if create:
            flags |= os.O_CREAT
        no_follow = getattr(os, "O_NOFOLLOW", None)
        non_block = getattr(os, "O_NONBLOCK", None)
        if no_follow is None or non_block is None:
            raise RuntimeError("当前平台不支持安全文件打开")
        descriptor = os.open(path, flags | no_follow | non_block, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("存储条目必须是普通文件")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _open_windows(path: Path, *, writable: bool, create: bool) -> int:
    # 仅 Windows 加载原生绑定；OPEN_REPARSE_POINT 防止检查后替换链接的竞态。
    win32file = import_module("win32file")
    win32con = import_module("win32con")
    pywintypes = import_module("pywintypes")
    msvcrt = import_module("msvcrt")
    access = win32con.GENERIC_READ | (win32con.GENERIC_WRITE if writable else 0)
    try:
        handle = win32file.CreateFile(
            str(path),
            access,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
            None,
            win32con.OPEN_ALWAYS if create else win32con.OPEN_EXISTING,
            win32con.FILE_FLAG_OPEN_REPARSE_POINT | win32con.FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
    except pywintypes.error as exc:
        if exc.winerror in (2, 3):
            raise FileNotFoundError(errno.ENOENT, "存储文件不存在", str(path)) from exc
        raise OSError(errno.EACCES if exc.winerror == 5 else errno.EIO, "存储文件无法打开") from exc
    transferred = False
    try:
        attributes = win32file.GetFileInformationByHandle(handle)[0]
        if attributes & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
            raise ValueError("存储条目必须是普通文件，不得包含符号链接或重解析点")
        flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
        descriptor = int(msvcrt.open_osfhandle(int(handle), flags))
        handle.Detach()
        transferred = True
        return descriptor
    finally:
        if not transferred:
            handle.Close()


def sync_directory(path: Path) -> None:
    """Windows CRT 不支持目录 fsync；文件内容仍在原子替换前完成 fsync。"""
    if IS_WINDOWS:
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
