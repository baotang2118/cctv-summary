import pytest

from cctv_summary import __version__
from cctv_summary.cli import main


def test_version_is_exposed():
    assert isinstance(__version__, str)
    assert __version__


def test_main_returns_zero(capsys):
    assert main([]) == 0
    assert "Hello, world!" in capsys.readouterr().out


def test_main_accepts_name(capsys):
    assert main(["--name", "tangd"]) == 0
    assert "Hello, tangd!" in capsys.readouterr().out


def test_version_flag_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out
