"""NVIDIA GPU backend via faster-whisper (CTranslate2)."""

import os
import threading

from faster_whisper import WhisperModel

DEFAULT_MODEL = os.environ.get("MODEL_ID", "Systran/faster-whisper-large-v3")
DEVICE = "cuda"
COMPUTE_TYPE = os.environ.get("COMPUTE_TYPE", "float16")
CACHE_DIR = "/models"


def _parse_models(default):
    """Build the selectable-model allowlist. Override via WHISPER_MODELS, a
    comma-separated list of `id` or `id|Human label` items."""
    raw = os.environ.get("WHISPER_MODELS", "").strip()
    if not raw:
        return default
    out = []
    for item in raw.split(","):
        mid, _, label = item.strip().partition("|")
        mid, label = mid.strip(), label.strip()
        if mid:
            out.append({"id": mid, "label": label or mid})
    return out or default


# The dropdown/allowlist. Only ids in here can be switched to from the GUI --
# an arbitrary `model` value from a request is ignored, not blindly downloaded.
MODELS = _parse_models([
    {"id": "Systran/faster-whisper-large-v3", "label": "Large v3 (best quality)"},
    {"id": "Systran/faster-whisper-medium", "label": "Medium (faster)"},
])

_model = None
_current_model = None
_infer_lock = threading.Lock()  # one shared model -- not safe for concurrent calls


def current_model():
    return _current_model


def load(model_id=None):
    """(Re)load the model for `model_id` (defaults to DEFAULT_MODEL). The slow
    construct/download happens lock-free; only the global swap holds _infer_lock,
    so an in-flight transcription finishes on the old model before we switch."""
    global _model, _current_model
    model_id = model_id or DEFAULT_MODEL
    m = WhisperModel(model_id, device=DEVICE, compute_type=COMPUTE_TYPE, download_root=CACHE_DIR)
    with _infer_lock:
        _model, _current_model = m, model_id
    return {"model": model_id, "device": DEVICE}


def transcribe_stream(audio, language, task, initial_prompt=None, hotwords=None):
    # faster-whisper yields segments as it decodes them, each carrying real
    # timestamps against the known total duration -- genuine incremental
    # progress, no manual chunking needed (unlike the Arc/OpenVINO engine).
    kwargs = {}
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    if hotwords:
        kwargs["hotwords"] = hotwords
    with _infer_lock:
        segments, info = _model.transcribe(audio, language=language or None, task=task, **kwargs)
        duration = info.duration or 1
        parts = []
        for seg in segments:
            parts.append(seg.text.strip())
            text = " ".join(parts)
            pct = max(1, min(99, int(seg.end / duration * 100)))
            yield {"progress": pct, "text": text, "done": False,
                   "new_segments": [{"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()}]}
    yield {"progress": 100, "text": text if parts else "", "new_segments": [], "done": True}


def transcribe(audio, language, task, initial_prompt=None, hotwords=None) -> str:
    *_, last = transcribe_stream(audio, language, task, initial_prompt, hotwords)
    return last["text"]
