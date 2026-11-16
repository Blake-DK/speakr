"""Intel Arc GPU backend via OpenVINO GenAI.

Pinned to openvino-genai==2026.2.1.0: on `master` this WhisperPipeline API
is being replaced by a generalized ASRPipeline. Don't bump the version pin
without re-checking the sample API at the new tag first.
"""

import logging
import os
import threading
import time

import openvino_genai
from huggingface_hub import snapshot_download

log = logging.getLogger("whisper.arc")

# Default to the fp16 model, not int8: int8 quantization measurably lowers
# transcription accuracy. Override the default with MODEL_ID; a faster/smaller
# variant (e.g. OpenVINO/whisper-large-v3-int8-ov) can also be picked at runtime
# from the GUI, which reloads the pipeline in-place -- see load()/current_model().
DEFAULT_MODEL = os.environ.get("MODEL_ID", "OpenVINO/whisper-large-v3-fp16-ov")
CACHE_DIR = "/models"
DEVICE = "GPU"
SAMPLE_RATE = 16000


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
    {"id": "OpenVINO/whisper-large-v3-fp16-ov", "label": "Large v3 (fp16 — best quality)"},
    {"id": "OpenVINO/whisper-large-v3-int8-ov", "label": "Large v3 (int8 — faster)"},
])
# WhisperPipeline.generate()'s streamer callback only works for short-form
# audio (< 30s); there's no incremental signal for longer clips otherwise.
# Chunking manually at the application level gives real, if slightly
# coarser-grained, per-chunk progress for clips of any length. We OVERLAP the
# windows by OVERLAP_SECONDS so a word straddling a cut appears whole in at
# least one window, then stitch consecutive results on their shared words --
# this recovers the boundary handling a naive back-to-back split would lose.
CHUNK_SECONDS = 28
OVERLAP_SECONDS = 4

_pipe = None
_current_model = None
_infer_lock = threading.Lock()  # one shared inference request -- not safe for concurrent calls


def current_model():
    return _current_model


def load(model_id=None):
    """(Re)load the pipeline for `model_id` (defaults to DEFAULT_MODEL). The slow
    download + compile happens lock-free; only the global swap holds _infer_lock,
    so an in-flight transcription finishes on the old pipe before we switch."""
    global _pipe, _current_model
    model_id = model_id or DEFAULT_MODEL
    log.info("loading model %s ...", model_id)
    model_dir = snapshot_download(model_id, cache_dir=CACHE_DIR)
    pipe = openvino_genai.WhisperPipeline(model_dir, DEVICE, CACHE_DIR=f"{CACHE_DIR}/ov_cache")
    with _infer_lock:
        _pipe, _current_model = pipe, model_id
    log.info("model ready: %s", model_id)
    return {"model": model_id, "device": DEVICE}


def _norm(word):
    return word.lower().strip(".,!?;:\"'()[]")


def _stitch(a, b):
    """Merge chunk text `b` onto accumulated text `a`, collapsing the words they
    share in the overlap region so the seam isn't duplicated."""
    if not a:
        return b
    if not b:
        return a
    aw, bw = a.split(), b.split()
    # Longest suffix of `a` that equals a prefix of `b` (word-level, punctuation-
    # and case-insensitive). Cap the search to the overlap window's worth of words.
    max_k = min(len(aw), len(bw), OVERLAP_SECONDS * 6)
    for k in range(max_k, 0, -1):
        if [_norm(w) for w in aw[-k:]] == [_norm(w) for w in bw[:k]]:
            return " ".join(aw + bw[k:])
    return " ".join(aw + bw)


def transcribe_stream(audio, language, task, initial_prompt=None, hotwords=None):
    kwargs = {"task": task}
    if language:
        kwargs["language"] = f"<|{language}|>"
    # Domain vocabulary (names, jargon): both are supported by the pinned
    # openvino_genai's WhisperGenerationConfig and bias decoding toward them.
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    if hotwords:
        kwargs["hotwords"] = hotwords

    chunk_len = CHUNK_SECONDS * SAMPLE_RATE
    stride = (CHUNK_SECONDS - OVERLAP_SECONDS) * SAMPLE_RATE
    starts = []
    s = 0
    while s < len(audio):
        starts.append(s)
        if s + chunk_len >= len(audio):  # this window already reaches the end
            break
        s += stride
    starts = starts or [0]
    chunks = [audio[s:s + chunk_len] for s in starts]
    log.info("%d chunk(s), %.1fs of audio total", len(chunks), len(audio) / SAMPLE_RATE)

    text = ""
    for i, chunk in enumerate(chunks):
        log.info("chunk %d/%d: waiting for GPU lock", i + 1, len(chunks))
        t0 = time.monotonic()
        with _infer_lock:
            log.info("chunk %d/%d: generating", i + 1, len(chunks))
            result = _pipe.generate(chunk.tolist(), **kwargs)
        log.info("chunk %d/%d: done in %.1fs", i + 1, len(chunks), time.monotonic() - t0)
        prev_len = len(text)
        text = _stitch(text, str(result).strip())
        done = i == len(chunks) - 1
        # Window-granularity timestamps: the words this window contributed
        # (post-stitch delta), spanning the window's slice of the audio. Coarser
        # than faster-whisper's native segments but stitch-safe at the seams.
        delta = text[prev_len:].strip()
        window_start = starts[i] / SAMPLE_RATE
        window_end = min((starts[i] + chunk_len), len(audio)) / SAMPLE_RATE
        new_segments = [{"start": round(window_start, 2), "end": round(window_end, 2), "text": delta}] if delta else []
        yield {"progress": 100 if done else int((i + 1) / len(chunks) * 100),
               "text": text, "new_segments": new_segments, "done": done}


def transcribe(audio, language, task, initial_prompt=None, hotwords=None) -> str:
    *_, last = transcribe_stream(audio, language, task, initial_prompt, hotwords)
    return last["text"]
