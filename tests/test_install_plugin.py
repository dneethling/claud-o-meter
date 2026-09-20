import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from install_plugin import install_launcher


def test_existing_widgets_are_kept_and_launcher_handles_quoted_paths(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    other = plugins / "weather.5m.sh"
    other.write_text("keep me")
    root = tmp_path / "Darren's app $test"
    (root / "plugins").mkdir(parents=True)
    (root / "plugins/claude-usage.5m.sh").write_text('printf "%s" "$CLAUDE_WIDGET_DIR"')
    install_launcher(plugins, root)
    result = subprocess.run(["bash", str(plugins / "claude-usage.5m.sh")], capture_output=True, text=True, check=True)
    assert result.stdout == str(root)
    assert other.read_text() == "keep me"
    install_launcher(plugins, root)  # repeat setup is safe


def test_unmanaged_widget_is_never_overwritten(tmp_path):
    target = tmp_path / "claude-usage.5m.sh"
    target.write_text("my customized widget")
    with pytest.raises(ValueError):
        install_launcher(tmp_path, tmp_path / "repo")
    assert target.read_text() == "my customized widget"


def test_symlink_is_never_followed_or_replaced(tmp_path):
    original = tmp_path / "original"
    original.write_text("keep")
    (tmp_path / "claude-usage.5m.sh").symlink_to(original)
    with pytest.raises(ValueError):
        install_launcher(tmp_path, tmp_path / "repo")
    assert original.read_text() == "keep"
