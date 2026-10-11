"""STUB (M5). Push-to-talk: record -> Parakeet ASR (:8002) -> grounded answer from
Lightning (at most 2 sentences, cited source_ids) -> TTS (:8003) -> play, with
barge-in. M1 does nothing and speaks nothing.
"""
from __future__ import annotations

from typing import Optional

STATUS = "not running (M5)"


async def answer(question_wav: bytes) -> Optional[dict]:
    """M1: returns None; no transcription, answer or audio is produced."""
    return None
