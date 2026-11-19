"""Trascrizione dei vocali con faster-whisper, in locale (nessun costo, l'audio non esce dal server)."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from .config import settings

log = logging.getLogger(__name__)

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel  # import lento: solo al primo vocale

            log.info("Carico il modello Whisper '%s' (la prima volta lo scarica)...",
                     settings.whisper_model)
            _model = WhisperModel(settings.whisper_model, device="auto", compute_type="int8")
        return _model


def transcribe_file(path: Path) -> str:
    model = _get_model()
    with _lock:  # un vocale alla volta: evita di saturare la CPU
        segments, _info = model.transcribe(
            str(path),
            language=settings.whisper_language or None,
            vad_filter=True,
            beam_size=5,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
    return text or "[vocale senza parlato riconoscibile]"


async def transcribe(path: Path) -> str:
    return await asyncio.to_thread(transcribe_file, path)
