"""
Tests for audio.tts — subprocess and piper-tts are mocked; no audio
hardware, network, or model files required.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from audio.radio import encode_wav


def _wav_bytes(n: int = 8_000, sr: int = 22_050) -> bytes:
    return encode_wav(np.zeros(n, dtype=np.float32), sr)


# ─────────────────────────── say backend ─────────────────────────────────────

class TestBackendSay:

    @patch('audio.tts.shutil.which', return_value='/usr/bin/say')
    @patch('audio.tts.subprocess.run')
    @patch('audio.tts.Path.read_bytes', return_value=None)
    def _call(self, text, mock_rb, mock_run, mock_which,
              wav_bytes=None):
        mock_rb.return_value = wav_bytes or _wav_bytes()
        from audio.tts import _backend_say
        return _backend_say(text)

    def test_returns_float32_array(self):
        samples, sr = self._call("Hello")
        assert samples.dtype == np.float32
        assert isinstance(sr, int)

    def test_raises_if_say_not_found(self):
        with patch('audio.tts.shutil.which', return_value=None):
            from audio.tts import _backend_say
            with pytest.raises(RuntimeError, match='`say`'):
                _backend_say("test")

    @patch('audio.tts.shutil.which', return_value='/usr/bin/say')
    @patch('audio.tts.subprocess.run')
    @patch('audio.tts.Path.read_bytes')
    def test_invokes_say_with_wave_format(self, mock_rb, mock_run, _):
        mock_rb.return_value = _wav_bytes()
        from audio.tts import _backend_say
        _backend_say("Test message")
        args = mock_run.call_args[0][0]
        assert 'say' in args
        assert '--file-format=WAVE' in args

    @patch('audio.tts.shutil.which', return_value='/usr/bin/say')
    @patch('audio.tts.subprocess.run')
    @patch('audio.tts.Path.read_bytes')
    def test_text_passed_to_say(self, mock_rb, mock_run, _):
        mock_rb.return_value = _wav_bytes()
        from audio.tts import _backend_say
        _backend_say("Hannover Ground D-EIYD request startup")
        args = mock_run.call_args[0][0]
        assert "Hannover Ground D-EIYD request startup" in args

    @patch('audio.tts.shutil.which', return_value='/usr/bin/say')
    @patch('audio.tts.subprocess.run')
    @patch('audio.tts.Path.read_bytes')
    def test_temp_file_cleaned_up_on_success(self, mock_rb, mock_run, _):
        mock_rb.return_value = _wav_bytes()
        created = []
        original_unlink = Path.unlink

        def track_unlink(self, *a, **kw):
            created.append(str(self))
            original_unlink(self, *a, **kw)

        with patch.object(Path, 'unlink', track_unlink):
            from audio.tts import _backend_say
            _backend_say("test")
        assert len(created) >= 1

    @patch('audio.tts.shutil.which', return_value='/usr/bin/say')
    @patch('audio.tts.subprocess.run', side_effect=Exception("say failed"))
    @patch('audio.tts.Path.read_bytes')
    def test_temp_file_cleaned_up_on_failure(self, mock_rb, mock_run, _):
        mock_rb.return_value = _wav_bytes()
        cleaned = []
        original_unlink = Path.unlink

        def track(self, *a, **kw):
            cleaned.append(str(self))
            original_unlink(self, *a, **kw)

        with patch.object(Path, 'unlink', track):
            from audio.tts import _backend_say
            with pytest.raises(Exception):
                _backend_say("test")
        assert len(cleaned) >= 1


# ─────────────────────────── synthesize (public API) ─────────────────────────

class TestSynthesize:

    @patch('audio.tts._kokoro_available', return_value=False)
    @patch('audio.tts._piper_available', return_value=False)
    @patch('audio.tts._backend_say')
    def test_auto_falls_back_to_say(self, mock_say, _piper, _kokoro):
        mock_say.return_value = (np.zeros(100, dtype=np.float32), 22_050)
        from audio.tts import synthesize
        samples, sr = synthesize("test", backend='auto')
        mock_say.assert_called_once()
        assert samples.dtype == np.float32

    @patch('audio.tts._kokoro_available', return_value=False)
    @patch('audio.tts._piper_available', return_value=True)
    @patch('audio.tts._backend_piper')
    def test_auto_prefers_piper_over_say(self, mock_piper, _piper, _kokoro):
        mock_piper.return_value = (np.zeros(100, dtype=np.float32), 22_050)
        from audio.tts import synthesize
        synthesize("test", backend='auto')
        mock_piper.assert_called_once()

    @patch('audio.tts._kokoro_available', return_value=True)
    @patch('audio.tts._backend_kokoro')
    def test_auto_prefers_kokoro_over_piper(self, mock_kokoro, _):
        mock_kokoro.return_value = (np.zeros(100, dtype=np.float32), 24_000)
        from audio.tts import synthesize
        synthesize("test", backend='auto')
        mock_kokoro.assert_called_once()

    @patch('audio.tts._backend_kokoro')
    def test_explicit_kokoro_backend(self, mock_kokoro):
        mock_kokoro.return_value = (np.zeros(100, dtype=np.float32), 24_000)
        from audio.tts import synthesize
        synthesize("test", backend='kokoro', voice='am_adam')
        mock_kokoro.assert_called_once_with("test", 'am_adam')

    @patch('audio.tts._backend_say')
    def test_explicit_say_backend(self, mock_say):
        mock_say.return_value = (np.zeros(100, dtype=np.float32), 22_050)
        from audio.tts import synthesize
        synthesize("test", backend='say')
        mock_say.assert_called_once()

    @patch('audio.tts._backend_piper')
    def test_explicit_piper_backend(self, mock_piper):
        mock_piper.return_value = (np.zeros(100, dtype=np.float32), 22_050)
        from audio.tts import synthesize
        synthesize("test", backend='piper', voice='en_US-lessac-medium')
        mock_piper.assert_called_once_with("test", 'en_US-lessac-medium')

    def test_unknown_backend_raises(self):
        from audio.tts import synthesize
        with pytest.raises(ValueError, match='Unknown TTS backend'):
            synthesize("test", backend='cosmic_ray')

    @patch('audio.tts._kokoro_available', return_value=False)
    @patch('audio.tts._piper_available', return_value=False)
    @patch('audio.tts._backend_say')
    def test_returns_tuple_of_array_and_int(self, mock_say, _piper, _kokoro):
        mock_say.return_value = (np.zeros(100, dtype=np.float32), 22_050)
        from audio.tts import synthesize
        result = synthesize("test")
        samples, sr = result
        assert isinstance(samples, np.ndarray)
        assert isinstance(sr, int)
