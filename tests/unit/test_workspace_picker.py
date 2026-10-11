import subprocess
from unittest.mock import Mock

import pytest

from camel_backend import workspace


@pytest.mark.parametrize("selected", ["", "selected-directory"])
def test_windows_picker_passes_initial_path_as_input(monkeypatch, tmp_path, selected):
    monkeypatch.setattr(workspace.sys, "platform", "win32")
    monkeypatch.setattr(workspace.shutil, "which", lambda _: "powershell.exe")
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, selected, ""))
    monkeypatch.setattr(workspace.subprocess, "run", runner)
    initial = tmp_path / "包含中文和'引号"
    result = workspace.NativeWorkspacePicker._select_sync(initial)
    assert result == (workspace.Path(selected).resolve() if selected else None)
    args, kwargs = runner.call_args
    assert args[0][:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA"]
    assert str(initial) not in args[0][-1]
    assert kwargs["input"] == str(initial.resolve())
    assert kwargs["encoding"] == "utf-8"


def test_windows_picker_error_is_explicit_and_does_not_echo_output(monkeypatch):
    monkeypatch.setattr(workspace.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        workspace.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 1, "", "secret")),
    )
    with pytest.raises(RuntimeError, match="Windows 工作区选择器") as error:
        workspace.NativeWorkspacePicker._select_windows(None)
    assert "secret" not in str(error.value)
