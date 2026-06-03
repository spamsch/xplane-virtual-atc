"""
Tests for audio.stt — all network and model calls mocked.
No faster-whisper download or OpenAI API key required.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from audio.radio import encode_wav

SR = 16_000


def _wav(secs: float = 1.0) -> bytes:
    return encode_wav(np.zeros(int(SR * secs), dtype=np.float32), SR)


def _mock_local_model(transcript: str = "D-EIYD request startup") -> MagicMock:
    seg      = MagicMock()
    seg.text = transcript
    model    = MagicMock()
    model.transcribe.return_value = ([seg], MagicMock())
    return model


# ──────────────────────── local backend (transcribe) ─────────────────────────

class TestTranscribeLocal:

    @patch('audio.stt._active_backend', return_value='local')
    @patch('audio.stt._load_local_model')
    def test_returns_string(self, mock_load, _):
        mock_load.return_value = _mock_local_model("Hannover Ground D-EIYD")
        from audio.stt import transcribe
        assert isinstance(transcribe(_wav()), str)

    @patch('audio.stt._active_backend', return_value='local')
    @patch('audio.stt._load_local_model')
    def test_strips_whitespace(self, mock_load, _):
        mock_load.return_value = _mock_local_model("  hello world  ")
        from audio.stt import transcribe
        assert transcribe(_wav()) == "hello world"

    @patch('audio.stt._active_backend', return_value='local')
    @patch('audio.stt._load_local_model')
    def test_joins_multiple_segments(self, mock_load, _):
        seg1, seg2 = MagicMock(), MagicMock()
        seg1.text, seg2.text = "Hannover Ground", "request startup"
        model = MagicMock()
        model.transcribe.return_value = ([seg1, seg2], MagicMock())
        mock_load.return_value = model
        from audio.stt import transcribe
        assert transcribe(_wav()) == "Hannover Ground request startup"

    @patch('audio.stt._active_backend', return_value='local')
    @patch('audio.stt._load_local_model')
    def test_callsign_in_prompt(self, mock_load, _):
        model = _mock_local_model("test")
        mock_load.return_value = model
        from audio.stt import transcribe
        transcribe(_wav(), callsign="D-EIYD")
        _, kwargs = model.transcribe.call_args
        assert "D-EIYD" in kwargs.get('initial_prompt', '')

    @patch('audio.stt._active_backend', return_value='local')
    @patch('audio.stt._load_local_model')
    def test_nato_in_prompt(self, mock_load, _):
        model = _mock_local_model("test")
        mock_load.return_value = model
        from audio.stt import transcribe
        transcribe(_wav())
        _, kwargs = model.transcribe.call_args
        prompt = kwargs.get('initial_prompt', '')
        assert 'Alpha' in prompt and 'squawk' in prompt

    @patch('audio.stt._active_backend', return_value='local')
    @patch('audio.stt._load_local_model')
    def test_language_english(self, mock_load, _):
        model = _mock_local_model("test")
        mock_load.return_value = model
        from audio.stt import transcribe
        transcribe(_wav())
        _, kwargs = model.transcribe.call_args
        assert kwargs.get('language') == 'en'

    @patch('audio.stt._active_backend', return_value='local')
    @patch('audio.stt._load_local_model')
    def test_empty_segments_returns_empty(self, mock_load, _):
        model = MagicMock()
        model.transcribe.return_value = ([], MagicMock())
        mock_load.return_value = model
        from audio.stt import transcribe
        assert transcribe(_wav()) == ""

    @patch('audio.stt._active_backend', return_value='local')
    @patch('audio.stt._load_local_model')
    def test_temp_file_cleaned_up(self, mock_load, _):
        mock_load.return_value = _mock_local_model("test")
        removed = []
        import pathlib
        original_unlink = pathlib.Path.unlink

        def track(self, *a, **kw):
            removed.append(str(self))
            original_unlink(self, *a, **kw)

        with patch.object(pathlib.Path, 'unlink', track):
            from audio.stt import transcribe
            transcribe(_wav())
        assert any('.wav' in p for p in removed)


# ──────────────────────── OpenAI backend (transcribe) ────────────────────────

class TestTranscribeOpenAI:

    @patch('audio.stt._active_backend', return_value='openai')
    @patch('audio.stt._transcribe_openai', return_value="Hannover Ground")
    def test_routes_to_openai(self, mock_api, _):
        from audio.stt import transcribe
        result = transcribe(_wav(), callsign="D-EIYD")
        mock_api.assert_called_once_with(_wav(), "D-EIYD")
        assert result == "Hannover Ground"

    @patch('audio.stt._active_backend', return_value='openai')
    @patch('audio.stt._transcribe_openai', return_value="  trimmed  ")
    def test_openai_result_stripped(self, mock_api, _):
        from audio.stt import transcribe
        assert transcribe(_wav()) == "trimmed"


# ──────────────────────── ElevenLabs backend (transcribe) ────────────────────

class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data
    def read(self):
        return self._data
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


class TestTranscribeElevenLabs:

    @patch('audio.stt._active_backend', return_value='elevenlabs')
    @patch('audio.stt._transcribe_elevenlabs', return_value="Hannover Tower")
    def test_routes_to_elevenlabs(self, mock_el, _):
        from audio.stt import transcribe
        assert transcribe(_wav(), callsign="D-EIYD") == "Hannover Tower"
        mock_el.assert_called_once()

    def test_parses_text_field_and_sends_model(self):
        import json, config as _cfg
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured['url'] = req.full_url
            captured['key'] = req.headers.get('Xi-api-key')
            captured['body'] = req.data
            return _FakeResp(json.dumps({"text": "D-EIYD ready for departure"}).encode())
        with patch('audio.stt.urllib.request.urlopen', side_effect=fake_urlopen), \
             patch.object(_cfg, 'ELEVENLABS_API_KEY', 'sk_el'), \
             patch.object(_cfg, 'ELEVENLABS_STT_MODEL', 'scribe_v1'):
            from audio.stt import _transcribe_elevenlabs
            text = _transcribe_elevenlabs(_wav(), callsign="D-EIYD")
        assert text == "D-EIYD ready for departure"
        assert captured['url'].endswith('/v1/speech-to-text')
        assert captured['key'] == 'sk_el'
        assert b'scribe_v1' in captured['body']


# ─────────────────────── backend selection (_active_backend) ─────────────────

class TestActiveBackend:

    @patch('audio.stt.config')
    def test_openai_when_key_set(self, mock_cfg):
        mock_cfg.OPENAI_API_KEY = 'sk-test'
        mock_cfg.ELEVENLABS_API_KEY = ''
        import os
        with patch.dict(os.environ, {'STT_BACKEND': 'auto'}):
            from audio.stt import _active_backend
            assert _active_backend() == 'openai'

    @patch('audio.stt.config')
    def test_elevenlabs_preferred_when_key_set(self, mock_cfg):
        mock_cfg.ELEVENLABS_API_KEY = 'sk_el'
        mock_cfg.OPENAI_API_KEY = 'sk-test'
        import os
        with patch.dict(os.environ, {'STT_BACKEND': 'auto'}):
            from audio.stt import _active_backend
            assert _active_backend() == 'elevenlabs'

    @patch('audio.stt.config')
    def test_local_when_no_key(self, mock_cfg):
        mock_cfg.OPENAI_API_KEY = ''
        mock_cfg.ELEVENLABS_API_KEY = ''
        import os
        with patch.dict(os.environ, {'STT_BACKEND': 'auto'}):
            from audio.stt import _active_backend
            assert _active_backend() == 'local'

    def test_explicit_override(self):
        import os
        with patch.dict(os.environ, {'STT_BACKEND': 'local'}):
            from audio.stt import _active_backend
            assert _active_backend() == 'local'


# ─────────────────────── local model loading ─────────────────────────────────

class TestLocalModel:

    def test_raises_without_faster_whisper(self):
        import audio.stt as stt_mod
        original = stt_mod._model
        stt_mod._model = None
        try:
            with patch.dict(sys.modules, {'faster_whisper': None}):
                with pytest.raises(RuntimeError, match='faster-whisper'):
                    stt_mod._load_local_model()
        finally:
            stt_mod._model = original

    def test_model_cached_after_first_load(self):
        import audio.stt as stt_mod
        original = stt_mod._model
        stt_mod._model = None
        try:
            mock_cls = MagicMock()
            mock_cls.return_value = MagicMock()
            fake_fw = MagicMock()
            fake_fw.WhisperModel = mock_cls
            with patch.dict(sys.modules, {'faster_whisper': fake_fw}):
                m1 = stt_mod._load_local_model()
                m2 = stt_mod._load_local_model()
            assert m1 is m2
            mock_cls.assert_called_once()
        finally:
            stt_mod._model = original
