"""
Tests for audio.stt — faster-whisper is mocked; no model download required.
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


def _mock_model(transcript: str = "D-EIYD request startup") -> MagicMock:
    seg      = MagicMock()
    seg.text = transcript
    model    = MagicMock()
    model.transcribe.return_value = ([seg], MagicMock())
    return model


# ─────────────────────────────── transcribe ──────────────────────────────────

class TestTranscribe:

    @patch('audio.stt._load_model')
    def test_returns_string(self, mock_load):
        mock_load.return_value = _mock_model("Hannover Ground D-EIYD")
        from audio.stt import transcribe
        result = transcribe(_wav())
        assert isinstance(result, str)

    @patch('audio.stt._load_model')
    def test_strips_leading_trailing_whitespace(self, mock_load):
        mock_load.return_value = _mock_model("  hello world  ")
        from audio.stt import transcribe
        assert transcribe(_wav()) == "hello world"

    @patch('audio.stt._load_model')
    def test_joins_multiple_segments(self, mock_load):
        seg1, seg2 = MagicMock(), MagicMock()
        seg1.text  = "Hannover Ground"
        seg2.text  = "request startup"
        model      = MagicMock()
        model.transcribe.return_value = ([seg1, seg2], MagicMock())
        mock_load.return_value = model
        from audio.stt import transcribe
        assert transcribe(_wav()) == "Hannover Ground request startup"

    @patch('audio.stt._load_model')
    def test_callsign_in_initial_prompt(self, mock_load):
        model = _mock_model("test")
        mock_load.return_value = model
        from audio.stt import transcribe
        transcribe(_wav(), callsign="D-EIYD")
        _, kwargs = model.transcribe.call_args
        assert "D-EIYD" in kwargs.get('initial_prompt', '')

    @patch('audio.stt._load_model')
    def test_no_callsign_still_has_nato_prompt(self, mock_load):
        model = _mock_model("test")
        mock_load.return_value = model
        from audio.stt import transcribe
        transcribe(_wav())
        _, kwargs = model.transcribe.call_args
        prompt = kwargs.get('initial_prompt', '')
        assert 'Alpha' in prompt
        assert 'squawk' in prompt

    @patch('audio.stt._load_model')
    def test_language_forced_to_english(self, mock_load):
        model = _mock_model("test")
        mock_load.return_value = model
        from audio.stt import transcribe
        transcribe(_wav())
        _, kwargs = model.transcribe.call_args
        assert kwargs.get('language') == 'en'

    @patch('audio.stt._load_model')
    def test_empty_segments_returns_empty_string(self, mock_load):
        model = MagicMock()
        model.transcribe.return_value = ([], MagicMock())
        mock_load.return_value = model
        from audio.stt import transcribe
        assert transcribe(_wav()) == ""

    @patch('audio.stt._load_model')
    def test_temp_file_removed_after_transcription(self, mock_load):
        mock_load.return_value = _mock_model("test")
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


# ─────────────────────────────── _load_model ─────────────────────────────────

class TestLoadModel:

    def test_raises_without_faster_whisper_installed(self):
        import audio.stt as stt_mod
        original = stt_mod._model
        stt_mod._model = None
        try:
            with patch.dict(sys.modules, {'faster_whisper': None}):
                with pytest.raises(RuntimeError, match='faster-whisper'):
                    stt_mod._load_model()
        finally:
            stt_mod._model = original

    def test_model_cached_after_first_load(self):
        import audio.stt as stt_mod
        original = stt_mod._model
        stt_mod._model = None
        try:
            mock_cls = MagicMock()
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            fake_fw = MagicMock()
            fake_fw.WhisperModel = mock_cls
            with patch.dict(sys.modules, {'faster_whisper': fake_fw}):
                m1 = stt_mod._load_model()
                m2 = stt_mod._load_model()
            assert m1 is m2
            mock_cls.assert_called_once()
        finally:
            stt_mod._model = original
