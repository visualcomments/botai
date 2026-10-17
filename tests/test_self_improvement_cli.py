import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import cli


def test_workspace_cli_dispatches_self_improve_parser(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(sys, "argv", ["cli.py", "self-improve", "status", "--root", str(tmp_path), "--json"])
    cli.main()
    output = capsys.readouterr().out
    assert '"enabled": true' in output
    assert '"network": false' in output


def test_self_improve_help_is_command_specific(capsys):
    from self_improve import main

    assert main(["help"]) == 0
    output = capsys.readouterr().out
    assert "capture" in output
    assert "review" in output
    assert "--si-action" not in output


def test_workspace_help_lists_self_improve_subcommand(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cli.py", "--help"])
    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 0
    assert "self-improve" in capsys.readouterr().out
