"""Self-hosted Whisper transcription microservice.

Shared FastAPI app for both GPU backends (Intel Arc via OpenVINO GenAI,
NVIDIA via faster-whisper) — only `engine_arc.py`/`engine_cuda.py` differ,
picked at build time via the `ENGINE` env var baked into each Dockerfile.
Each engine owns its own inference lock (concurrent calls to a single
shared pipeline aren't safe) and its own `transcribe_stream()` generator
that yields real {"progress": 0-100, "text": ...} events as work completes.
"""

import json
import logging
import os
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("whisper")

ENGINE = os.environ["ENGINE"]  # "arc" or "cuda", set by the Dockerfile
engine = __import__(f"engine_{ENGINE}")

SAMPLE_RATE = 16000  # what _decode_audio resamples everything to
OPENAI_RESPONSE_FORMATS = ("json", "text", "verbose_json")
SINGLE_SPEAKER_LABEL = "SPEAKER_00"  # what diarize=true puts on every segment (no real diarization)

app = FastAPI()
_state = {"ready": False, "error": None}
_model_lock = threading.Lock()  # serialize model switches; one active model at a time
_ALLOWED = {m["id"] for m in engine.MODELS}


@app.on_event("startup")
def _load():
    log.info("loading model (engine=%s)...", ENGINE)
    try:
        _state.update(engine.load())
        _state["ready"] = True
        log.info("model ready: %s", _state)
    except Exception as e:  # noqa: BLE001 — surfaced via /status, not a crash loop
        _state["error"] = str(e)
        log.exception("model load failed")


@app.get("/status")
def status():
    return {"engine": ENGINE, **_state}


@app.get("/models")
def models():
    """Selectable models for the GUI dropdown, plus the currently loaded one."""
    return {"models": engine.MODELS, "current": engine.current_model()}


def _ensure_model(requested: str | None):
    """Switch the loaded model to `requested` if it's a known, different model.
    Unknown/blank values are ignored (external OpenAI-compatible servers pass
    names like 'whisper-1' that don't apply to the bundled single-model service),
    preserving the historical accepted-and-ignored behaviour. The switch reloads
    the pipeline (~tens of seconds) and is serialized so concurrent requests that
    ask for the same new model don't reload it twice."""
    if not requested or requested not in _ALLOWED or requested == engine.current_model():
        return
    with _model_lock:
        if requested == engine.current_model():  # loaded while we waited for the lock
            return
        log.info("switching model: %s -> %s", engine.current_model(), requested)
        _state["ready"] = False
        try:
            info = engine.load(requested)
        except Exception as e:  # noqa: BLE001 — surface as 503, keep the service alive
            _state["error"] = str(e)
            log.exception("model switch failed")
            raise HTTPException(status_code=503, detail=f"Could not load model {requested}: {e}")
        _state.update(info)
        _state["error"] = None
        _state["ready"] = True


def _decode_audio(file: UploadFile) -> np.ndarray:
    suffix = Path(file.filename or "in").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix) as src, tempfile.NamedTemporaryFile(suffix=".wav") as dst:
        data = file.file.read()
        src.write(data)
        src.flush()
        log.info("decoding %s (%d bytes)", file.filename, len(data))
        try:
            subprocess.run(
                # -protocol_whitelist file: ffmpeg picks the input demuxer from file
                # *content*, not extension — an uploaded "clip.wav" could actually be
                # an HLS playlist or concat script referencing http(s):// or other
                # file:// paths. Without this, that's an SSRF/local-file-read vector.
                ["ffmpeg", "-y", "-protocol_whitelist", "file", "-i", src.name, "-ar", "16000", "-ac", "1", "-f", "wav", dst.name],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            log.error("ffmpeg failed for %s: %s", file.filename, e.stderr.decode(errors="replace")[:500])
            raise HTTPException(status_code=400, detail=f"Could not decode audio/video: {e.stderr.decode(errors='replace')[:500]}")

        with wave.open(dst.name, "rb") as w:
            frames = w.readframes(w.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        log.info("decoded %s: %.1fs of audio", file.filename, len(audio) / 16000)
        return audio


def _load_audio(file: UploadFile, model: str | None) -> np.ndarray:
    """Decode the upload, switch to the requested model if needed, and make sure
    the pipeline is ready. Shared by every transcription route."""
    audio = _decode_audio(file)
    _ensure_model(model)  # may reload the pipeline before inference starts
    if not _state["ready"]:
        raise HTTPException(status_code=503, detail=f"Model not loaded: {_state.get('error') or 'still starting up'}")
    return audio


def _normalize_and_transcribe(file: UploadFile, language: str | None, task: str, model: str | None = None,
                              initial_prompt: str | None = None, hotwords: str | None = None) -> str:
    audio = _load_audio(file, model)
    return engine.transcribe(audio, language, task or "transcribe", initial_prompt, hotwords)


def _transcribe_verbose(file: UploadFile, language: str | None, task: str, model: str | None = None,
                        initial_prompt: str | None = None, hotwords: str | None = None,
                        speaker: str | None = None) -> dict:
    """The full transcript plus timestamped segments, in the shape of OpenAI's
    `verbose_json`. Segments come from the engine's own stream: per sentence on
    CUDA, per ~28s window on Arc. Arc's windows overlap by a few seconds, so each
    segment's end is clamped to the next segment's start to keep the timeline
    in order. With `speaker`, every segment carries that label."""
    audio = _load_audio(file, model)
    text, segments = "", []
    for event in engine.transcribe_stream(audio, language, task or "transcribe", initial_prompt, hotwords):
        segments.extend(event.get("new_segments") or [])
        text = event.get("text", text)
    for current, following in zip(segments, segments[1:]):
        if current["end"] > following["start"]:
            current["end"] = following["start"]
    return {
        "task": task or "transcribe",
        "language": language or None,  # no language detection here: echo what was asked for
        "duration": round(len(audio) / SAMPLE_RATE, 2),
        "text": text,
        "segments": [
            {"id": i, "start": s["start"], "end": s["end"], "text": s["text"],
             **({"speaker": speaker} if speaker else {})}
            for i, s in enumerate(segments)
        ],
    }


@app.post("/asr", response_class=PlainTextResponse)
def asr(file: UploadFile, language: str | None = Form(None), task: str = Form("transcribe"), model: str | None = Form(None),
        initial_prompt: str | None = Form(None), hotwords: str | None = Form(None)):
    return _normalize_and_transcribe(file, language, task, model, initial_prompt, hotwords)


@app.post("/asr/json")
def asr_json(file: UploadFile, language: str | None = Form(None), task: str = Form("transcribe"), model: str | None = Form(None),
             initial_prompt: str | None = Form(None), hotwords: str | None = Form(None)):
    return {"text": _normalize_and_transcribe(file, language, task, model, initial_prompt, hotwords)}


@app.post("/v1/audio/transcriptions")
def openai_compatible(
    file: UploadFile,
    model: str | None = Form(None),  # honored if it names a known bundled model; otherwise ignored
    language: str | None = Form(None),
    task: str = Form("transcribe"),
    prompt: str | None = Form(None),  # OpenAI's name for the initial prompt
    response_format: str | None = Form(None),  # json (default), text or verbose_json
    initial_prompt: str | None = Form(None),
    hotwords: str | None = Form(None),
    # There is no diarization here. Clients that store timestamps only for labelled
    # segments (Speakr's openasr connector asks diarize=true) get one speaker,
    # SPEAKER_00, on every verbose_json segment. Their other diarization fields
    # (speakers, return_speaker_embeddings) are not declared, so FastAPI ignores them.
    diarize: bool = Form(False),
):
    fmt = (response_format or "json").strip().lower()
    if fmt not in OPENAI_RESPONSE_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported response_format {response_format!r}: use {', '.join(OPENAI_RESPONSE_FORMATS)}",
        )
    initial_prompt = initial_prompt or prompt  # an explicit initial_prompt wins
    if fmt == "verbose_json":
        return _transcribe_verbose(file, language, task, model, initial_prompt, hotwords,
                                   speaker=SINGLE_SPEAKER_LABEL if diarize else None)
    text = _normalize_and_transcribe(file, language, task, model, initial_prompt, hotwords)
    return PlainTextResponse(text) if fmt == "text" else {"text": text}


@app.post("/asr/stream")
def asr_stream(file: UploadFile, language: str | None = Form(None), task: str = Form("transcribe"), model: str | None = Form(None),
               initial_prompt: str | None = Form(None), hotwords: str | None = Form(None)):
    """NDJSON progress stream: one {"progress": 0-100, "text": "...", "new_segments":
    [{"start", "end", "text"}]} line per completed chunk/segment, ending with
    {"progress": 100, ..., "done": true}. Timestamps are per-segment on CUDA
    (faster-whisper) and per-~28s-window on Arc (OpenVINO)."""
    audio = _load_audio(file, model)

    def gen():
        log.info("transcribe start: %s", file.filename)
        try:
            for event in engine.transcribe_stream(audio, language, task or "transcribe", initial_prompt, hotwords):
                yield json.dumps(event) + "\n"
        except Exception:
            log.exception("transcribe failed: %s", file.filename)
            raise
        log.info("transcribe done: %s", file.filename)

    return StreamingResponse(gen(), media_type="application/x-ndjson")
