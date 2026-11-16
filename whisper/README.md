# Whisper GPU transcription service

Part of this fork's studio add-on, not of upstream Speakr. Speakr uses it
through its `openasr` connector (see "Wiring into Speakr" below).

A small self-hosted Whisper REST API, bundled here as two GPU-backed variants:

- **Arc** (`whisper-arc`) — Intel Arc GPU (tested against a B580/Battlemage), inference via [OpenVINO GenAI](https://github.com/openvinotoolkit/openvino.genai).
- **CUDA** (`whisper-cuda`) — NVIDIA GPU, inference via [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2).

Both download a pre-converted model from HuggingFace on first start (cached
in a named volume — no runtime PyTorch→IR/CTranslate2 conversion), and run
the actual encoder+decoder pass on the GPU, not just CPU with GPU-assisted
preprocessing.

Only run **one** of these on a given host — pick the profile matching that
host's GPU vendor.

## API

| Route | Notes |
|---|---|
| `GET /status` | `{"engine": "arc"\|"cuda", "ready": bool, "model": ..., "device": ..., "error": ...}` |
| `GET /models` | `{"models": [{"id","label"}], "current": ...}` — the switchable-model allowlist + the loaded one, for a client's model picker |
| `POST /asr` | multipart `file` (+ optional `language`, `task`: `transcribe`\|`translate`) → plain text |
| `POST /asr/json` | same input → `{"text": "..."}` |
| `POST /v1/audio/transcriptions` | OpenAI-compatible shape (`file`, optional `model`/`language`/`task`/`prompt`/`response_format`). `response_format`: `json` (default) → `{"text": "..."}`; `text` → plain text; `verbose_json` → `{"task", "language", "duration", "text", "segments": [{"id", "start", "end", "text"}]}`, with the same segment granularity as `/asr/stream` (on Arc, overlapping windows are trimmed so each segment ends where the next starts). `prompt` is used as the initial prompt when no `initial_prompt` is sent. `language` is echoed back, not detected. Other formats get a 400. There is no diarization: `diarize=true` with `verbose_json` puts `"speaker": "SPEAKER_00"` on every segment, for clients that keep timestamps only for labelled segments (Speakr); other diarization fields such as `speakers` are ignored. |
| `POST /asr/stream` | same input, NDJSON response: one `{"progress": 0-100, "text": "...", "new_segments": [{"start", "end", "text"}], "done": false}` line per completed chunk/segment, ending with `"done": true`. Segment timestamps are per-sentence on CUDA, per-~28s-window on Arc. For clients that show transcription progress. |

All transcription endpoints also accept optional `initial_prompt` and `hotwords`
form fields (domain vocabulary — names, jargon — that biases decoding). Speakr's
`openasr` connector sends its hotwords and initial prompt as `prompt`.

Audio or video of any format ffmpeg can decode is accepted; it's normalized
to 16kHz mono before inference.

```bash
curl -F file=@clip.wav -F language=en http://<host>:9001/asr
curl -F file=@clip.wav http://<host>:9001/asr/json
curl -F file=@clip.wav http://<host>:9001/v1/audio/transcriptions
curl -F file=@clip.wav -F response_format=verbose_json http://<host>:9001/v1/audio/transcriptions
curl -N -F file=@clip.wav http://<host>:9001/asr/stream
```

Tests (no GPU or model needed; a fake engine stands in), from the repo root:

```bash
docker run --rm --memory 512m -v "$PWD/whisper":/w:ro -w /w \
  ghcr.io/blake-dk/speakr-whisper-arc:latest python3 tests/test_openai_compat.py
```

### Progress granularity

- **CUDA (faster-whisper)**: real per-segment progress as the model decodes them, using each segment's timestamp against the known audio duration.
- **Arc (OpenVINO GenAI)**: `WhisperPipeline`'s streamer callback only supports short-form audio (< 30s), so long clips are split into ~28s chunks at the application level and processed sequentially — real, if coarser-grained, progress. This trades a little cross-chunk-boundary accuracy (vs. OpenVINO's built-in long-form handling) for genuine progress visibility; fine for voice/tone samples, not intended for anything requiring maximum transcription accuracy at chunk seams.

## Wiring into Speakr

Speakr reaches this service through its `openasr` transcription connector.
Run both on one Docker network and set, in Speakr's environment:

```
TRANSCRIPTION_CONNECTOR=openasr
TRANSCRIPTION_BASE_URL=http://whisper-arc:8000/v1
TRANSCRIPTION_MODEL=OpenVINO/whisper-large-v3-fp16-ov
ASR_DIARIZE=true
```

`ASR_DIARIZE=true` matters: Speakr keeps segment timestamps (and with them
click-to-seek in the transcript) only for segments that carry a speaker label,
and this service labels every segment `SPEAKER_00` when asked to diarize. It
does not tell speakers apart. No API key is needed unless a proxy in front
asks for one.

## Intel Arc (`whisper-arc`)

**Host prerequisites:**
- `/dev/dri` present with the Arc GPU passed through (Proxmox: PCI passthrough to the VM; bare metal: just needs the kernel driver).
- Ubuntu 24.04 (noble) host, kernel 6.12+ (HWE) recommended. **Known risk:** Battlemage (B580) kernel/firmware support on stock 24.04 LTS kernels has been inconsistent — check `uname -r` and `dmesg | grep -i xe` on the host before assuming the container alone gets the GPU enumerated. If the GPU doesn't show up, the fix is a host kernel/firmware update, not a container change.

The image is published as `ghcr.io/blake-dk/speakr-whisper-arc` by
`.github/workflows/studio-publish.yml`. Run it with the GPU's device nodes and a
folder for the model cache:

```bash
docker run -d --name whisper-arc --device /dev/dri -v /path/to/models:/models \
  ghcr.io/blake-dk/speakr-whisper-arc:latest
docker logs -f whisper-arc   # wait for GET /status to report ready: true
```

Default model: `OpenVINO/whisper-large-v3-fp16-ov` (override with `MODEL_ID`; e.g. `OpenVINO/whisper-large-v3-int8-ov` for faster/smaller inference at some accuracy cost).

### Switching models from the GUI

The active model can be changed at runtime by sending a different allowed
`model` with a request (Speakr: `TRANSCRIPTION_MODEL`), no rebuild needed. A
different model makes the next transcription reload the pipeline in-place (~tens of seconds, cached after
the first download); `GET /status` reports the newly loaded model.

Only models in the allowlist can be selected — an arbitrary `model` value in a
request is ignored, never blindly downloaded. The allowlist defaults to the two
`large-v3` builds above and is overridable with the **`WHISPER_MODELS`** env var:
a comma-separated list of `id` or `id|Human label` entries, e.g.

```
WHISPER_MODELS=OpenVINO/whisper-large-v3-fp16-ov|Large v3 (fp16),OpenVINO/whisper-large-v3-int8-ov|Large v3 (int8)
```

## NVIDIA (`whisper-cuda`)

**Host prerequisites:**
- NVIDIA driver + [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) installed on the host, with the `nvidia` Docker runtime configured.
- Driver new enough for CUDA 12.6 (the base image's CUDA/cuDNN version).

No image is published for this variant; build it from `whisper/` with
`Dockerfile.cuda` on a machine with the NVIDIA toolchain.

Default model: `Systran/faster-whisper-large-v3` (override with `MODEL_ID`; `COMPUTE_TYPE` defaults to `float16`).

## Verification checklist

Run for real before trusting either variant — GPU inference failing silently
back to CPU is easy to miss otherwise:

1. Build + start; `GET /status` reports `ready: true`.
2. **Arc:** `docker exec whisper-arc clinfo` should list the Arc device (look for `Device Name ... Intel(R) Graphics [0xe20b]` — that PCI ID is the B580). **CUDA:** `docker exec whisper-cuda nvidia-smi` lists the GPU.
3. Send a real test clip to `/asr` and confirm the transcription is actually correct.
4. While a transcription request runs, watch GPU activity **on the host**:
   - **CUDA:** `nvidia-smi -l 1` (or `dmon`) — utilization% must spike during the request.
   - **Arc:** `intel_gpu_top` only supports the older `i915` driver, not the `xe` driver Battlemage (and other recent Intel GPUs) use — on an `xe`-driver host it reports "No device filter specified and no discrete/integrated i915 devices found" and is not a usable signal. Use the GPU's RAPL energy counter instead (no root required):
     ```bash
     E=/sys/class/drm/card0/device/hwmon/hwmon0/energy1_input
     a=$(cat $E); t1=$(date +%s.%N); sleep 3; b=$(cat $E); t2=$(date +%s.%N)
     python3 -c "print(f'{($b-$a)/1e6/($t2-$t1):.2f} W')"   # idle baseline
     # then repeat the same 4 lines wrapped around a curl request to /asr instead of `sleep 3`
     ```
     On the reference build this showed ~36W idle vs. ~57W during a 140s-audio transcription request (finished in 6.6s — ~21x realtime, itself further evidence this wasn't running on CPU).
5. **Arc-specific known issue:** `vainfo` fails on this driver/media-runtime combination (`iHD_drv_video.so has no function __vaDriverInit_1_0`) — the `intel-media-va-driver-non-free` version currently in Intel's noble "client" channel (24.3.4) predates full Battlemage VA-API support. This is harmless for transcription: OpenVINO's GPU plugin uses OpenCL/Level-Zero (confirmed working via `clinfo`), not VA-API — VA-API is only for hardware video decode/encode, which this service doesn't use. Revisit if Intel ships a newer media-driver build.

## Notes

- `ENGINE` (`arc`/`cuda`) is baked into each image at build time via `ENV`, not a runtime knob — the two variants have genuinely different dependency stacks (openvino-genai vs. faster-whisper/CUDA), so this isn't meant to be switched without rebuilding.
- No auth/TLS here — put this behind a reverse proxy, or keep it on a private network that only the app can reach.
