import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from widget_preferences import set_preference


def test_preserves_auth_comments_and_unrelated_settings(tmp_path):
    config = tmp_path / "config"
    original = "# comment\nCOOKIE=fake-test-cookie\nUSAGE_URL=https://example.invalid\nTHEME=semantic\nAUTO_UPDATE=1\n"
    config.write_text(original)
    set_preference("THEME", "minimal", config)
    assert config.read_text() == original.replace("THEME=semantic", "THEME=minimal")
    assert os.stat(config).st_mode & 0o777 == 0o600


def test_adds_setting_with_missing_final_newline(tmp_path):
    config = tmp_path / "config"
    config.write_text("COOKIE=fake-test-cookie")
    set_preference("DETAIL_LEVEL", "compact", config)
    assert config.read_text() == "COOKIE=fake-test-cookie\nDETAIL_LEVEL=compact\n"


def test_duplicate_setting_becomes_one_unambiguous_value(tmp_path):
    config = tmp_path / "config"
    config.write_text("THEME=semantic\n# keep\nTHEME=minimal\n")
    set_preference("THEME", "colorblind", config)
    assert config.read_text() == "THEME=colorblind\n# keep\n"


@pytest.mark.parametrize("key,value", [("COOKIE", "oops"), ("THEME", "$(oops)"), ("AUTO_UPDATE", "1")])
def test_rejects_non_display_writes(tmp_path, key, value):
    config = tmp_path / "config"
    config.write_text("COOKIE=fake-test-cookie\n")
    with pytest.raises(ValueError):
        set_preference(key, value, config)
    assert config.read_text() == "COOKIE=fake-test-cookie\n"


def test_missing_config_is_not_replaced_by_incomplete_config(tmp_path):
    config = tmp_path / "config"
    with pytest.raises(FileNotFoundError):
        set_preference("THEME", "minimal", config)
    assert not config.exists()
