"""The OpenAI-compatible route: response formats, segments and prompt mapping.

Needs no GPU and no model: a fake engine stands in for engine_arc/engine_cuda,
and the route function is called directly (the image has no httpx for
FastAPI's TestClient). Run it in the whisper image, from the repo root:

    docker run --rm --memory 512m -v "$PWD/whisper":/w:ro -w /w \
      ghcr.io/blake-dk/speakr-whisper-arc:latest python3 tests/test_openai_compat.py
"""
import io
import os
import sys
import types

os.environ["ENGINE"] = "fake"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

calls: list[dict] = []


def _transcribe_stream(audio, language, task, initial_prompt=None, hotwords=None):
    calls.append({"language": language, "task": task, "initial_prompt": initial_prompt, "hotwords": hotwords})
    # Two overlapping windows, the way the Arc engine yields them.
    yield {"progress": 50, "text": "hello there", "done": False,
           "new_segments": [{"start": 0.0, "end": 28.0, "text": "hello there"}]}
    yield {"progress": 100, "text": "hello there general kenobi", "done": True,
           "new_segments": [{"start": 24.0, "end": 40.0, "text": "general kenobi"}]}


def _transcribe(audio, language, task, initial_prompt=None, hotwords=None):
    *_, last = _transcribe_stream(audio, language, task, initial_prompt, hotwords)
    return last["text"]


fake = types.ModuleType("engine_fake")
fake.MODELS = [{"id": "fake-model", "label": "Fake"}]
fake.current_model = lambda: "fake-model"
fake.load = lambda model_id=None: {"model": "fake-model", "device": "CPU"}
fake.transcribe_stream = _transcribe_stream
fake.transcribe = _transcribe
sys.modules["engine_fake"] = fake

import numpy as np  # noqa: E402
from fastapi import HTTPException, UploadFile  # noqa: E402
from fastapi.responses import PlainTextResponse  # noqa: E402

import server  # noqa: E402

server._state["ready"] = True
server._decode_audio = lambda file: np.zeros(server.SAMPLE_RATE * 40, dtype=np.float32)


def call(**overrides):
    args = {"model": None, "language": None, "task": "transcribe", "prompt": None,
            "response_format": None, "initial_prompt": None, "hotwords": None, "diarize": False}
    args.update(overrides)
    calls.clear()
    return server.openai_compatible(file=UploadFile(file=io.BytesIO(b"x"), filename="clip.wav"), **args)


# Default and "json": the historical {"text"} shape, nothing else.
assert call() == {"text": "hello there general kenobi"}
assert call(response_format="json") == {"text": "hello there general kenobi"}

# "text": plain text body.
resp = call(response_format="text")
assert isinstance(resp, PlainTextResponse)
assert resp.body == b"hello there general kenobi"

# "verbose_json": text, duration, the requested language, ordered segments.
out = call(response_format="verbose_json", language="en")
assert out["text"] == "hello there general kenobi"
assert out["language"] == "en"
assert out["task"] == "transcribe"
assert out["duration"] == 40.0
assert out["segments"] == [
    {"id": 0, "start": 0.0, "end": 24.0, "text": "hello there"},  # end clamped to the next start
    {"id": 1, "start": 24.0, "end": 40.0, "text": "general kenobi"},
], out["segments"]
assert call(response_format="VERBOSE_JSON")["language"] is None  # case-insensitive; no language asked
assert all("speaker" not in s for s in out["segments"])  # no diarize: no speaker key, as in 0.30.4

# diarize=true: one speaker, SPEAKER_00, on every verbose_json segment; other formats unchanged.
out = call(response_format="verbose_json", diarize=True)
assert [s["speaker"] for s in out["segments"]] == ["SPEAKER_00", "SPEAKER_00"], out["segments"]
assert [(s["start"], s["end"], s["text"]) for s in out["segments"]] == [
    (0.0, 24.0, "hello there"), (24.0, 40.0, "general kenobi")]
assert call(diarize=True) == {"text": "hello there general kenobi"}
assert call(response_format="text", diarize=True).body == b"hello there general kenobi"

# `prompt` becomes the initial prompt; an explicit initial_prompt wins; hotwords pass through.
call(prompt="Bramble09, KaneFinch")
assert calls == [{"language": None, "task": "transcribe", "initial_prompt": "Bramble09, KaneFinch", "hotwords": None}]
call(prompt="ignored", initial_prompt="kept", hotwords="hot")
assert calls[0]["initial_prompt"] == "kept" and calls[0]["hotwords"] == "hot"

# Unknown formats are refused before any work is done.
try:
    call(response_format="srt")
except HTTPException as e:
    assert e.status_code == 400 and "srt" in e.detail
else:
    raise AssertionError("srt should be refused")
assert calls == []

print("ok: openai-compatible route")
