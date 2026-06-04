"""Tests for config persistence helpers (set_env / set_xplane_path)."""

import os
from pathlib import Path

import pytest

import config


@pytest.fixture
def restore_config():
    """Snapshot + restore the config globals these tests mutate."""
    keys = ["ELEVENLABS_API_KEY", "OPENAI_API_KEY", "XPLANE_BASE",
            "APT_DAT_PATHS", "XPLANE_PTT_DATAREF", "_env_file"]
    snap = {k: getattr(config, k) for k in keys}
    env_snap = dict(os.environ)
    yield
    for k, v in snap.items():
        setattr(config, k, v)
    os.environ.clear()
    os.environ.update(env_snap)


class TestSetEnv:
    def test_updates_module_and_environ(self, tmp_path, restore_config):
        config._env_file = tmp_path / ".env"
        config._env_file.write_text("ELEVENLABS_API_KEY=old\n")
        config.set_env("ELEVENLABS_API_KEY", "sk_new")
        assert config.ELEVENLABS_API_KEY == "sk_new"
        assert os.environ["ELEVENLABS_API_KEY"] == "sk_new"
        assert "ELEVENLABS_API_KEY=sk_new" in config._env_file.read_text()

    def test_preserves_other_lines(self, tmp_path, restore_config):
        config._env_file = tmp_path / ".env"
        config._env_file.write_text("# header\nFOO=1\nELEVENLABS_API_KEY=old\n")
        config.set_env("ELEVENLABS_API_KEY", "sk_new")
        text = config._env_file.read_text()
        assert "# header" in text and "FOO=1" in text
        assert "old" not in text

    def test_appends_new_key(self, tmp_path, restore_config):
        config._env_file = tmp_path / ".env"
        config._env_file.write_text("FOO=1\n")
        config.set_env("OPENAI_API_KEY", "sk-x")
        assert "OPENAI_API_KEY=sk-x" in config._env_file.read_text()

    def test_chmod_600(self, tmp_path, restore_config):
        config._env_file = tmp_path / ".env"
        config._env_file.write_text("")
        config.set_env("ELEVENLABS_API_KEY", "sk_secret")
        assert (config._env_file.stat().st_mode & 0o777) == 0o600

    def test_strips_whitespace(self, tmp_path, restore_config):
        config._env_file = tmp_path / ".env"
        config._env_file.write_text("")
        config.set_env("ELEVENLABS_API_KEY", "  sk_trim  ")
        assert config.ELEVENLABS_API_KEY == "sk_trim"


class TestResolveElevenLabsVoice:
    def test_name_maps_to_id(self):
        assert config.resolve_elevenlabs_voice("daniel") == "onwK4e9ZLuTAKqWW03F9"

    def test_name_is_case_insensitive(self):
        assert config.resolve_elevenlabs_voice("Brian") == config.ELEVENLABS_VOICE_LIBRARY["brian"]
        assert config.resolve_elevenlabs_voice("  GEORGE  ") == config.ELEVENLABS_VOICE_LIBRARY["george"]

    def test_raw_id_passes_through(self):
        raw = "AbCdEf1234567890raw"
        assert config.resolve_elevenlabs_voice(raw) == raw

    def test_empty_defaults_to_daniel(self):
        assert config.resolve_elevenlabs_voice("") == config.ELEVENLABS_VOICE_LIBRARY["daniel"]
        assert config.resolve_elevenlabs_voice(None) == config.ELEVENLABS_VOICE_LIBRARY["daniel"]

    def test_default_voice_id_is_daniel(self):
        # The resolved module default must be a real id, never the name.
        assert config.ELEVENLABS_VOICE_ID == config.ELEVENLABS_VOICE_LIBRARY["daniel"]


class TestPilotPool:
    def test_default_pool_has_ten_voices(self):
        assert len(config.ELEVENLABS_PILOT_POOL) == 10

    def test_pool_excludes_controller_default(self):
        # Other traffic must never sound like the controller.
        assert config.ELEVENLABS_VOICE_LIBRARY["daniel"] not in config.ELEVENLABS_PILOT_POOL

    def test_pool_ids_are_unique(self):
        assert len(set(config.ELEVENLABS_PILOT_POOL)) == len(config.ELEVENLABS_PILOT_POOL)

    def test_openai_pool_present(self):
        assert "onyx" in config.OPENAI_PILOT_POOL


class TestSetXPlanePath:
    def test_recomputes_apt_paths(self, tmp_path, restore_config):
        base = tmp_path / "X-Plane 12"
        config.set_xplane_path(str(base))
        assert config.XPLANE_BASE == base
        assert all(str(base) in str(p) for p in config.APT_DAT_PATHS)
        assert any(p.name == "apt.dat" for p in config.APT_DAT_PATHS)
