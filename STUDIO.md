# The studio fork

This repository is a fork of [Speakr](https://github.com/murtaza-nasir/speakr)
with one add-on, **studio**, kept apart from upstream's code so that new
upstream releases merge cleanly. Speakr's own documentation is in
[README.md](README.md) and [docs/](docs/); everything below is about the fork.

The fork is published under the same licence as Speakr, the GNU AGPL v3
([LICENSE](LICENSE)). The Projects page links here, so the people who use a
site running this code can get its source.

## What the add-on adds

- `/studio`: the Projects page (empty for now), linked from the user menu.
- `GET /api/studio/health`: `{"status": "ok"}`, for monitoring.
- `whisper/`: a Whisper transcription service for Intel Arc (and NVIDIA) GPUs
  with an OpenAI-compatible route, which Speakr reaches through its `openasr`
  connector. See [whisper/README.md](whisper/README.md).

## Where it lives

| path | what |
|---|---|
| `src/studio/` | blueprint, strings, database tables (`studio_*`) |
| `templates/studio/`, `static/js/studio/` | the add-on's pages |
| `static/locales-studio/` | the add-on's interface strings |
| `tests/studio/` | the add-on's tests (run with upstream's suite) |
| `whisper/` | the transcription service |
| `Dockerfile.studio` | the app image: upstream's lite image + the add-on's extras |
| `.github/workflows/studio-publish.yml` | builds and publishes both images |

Upstream files the add-on touches, each in one marked block:

- `src/app.py`: registers the studio blueprint;
- `src/models/__init__.py`: imports `src.studio.models`;
- `templates/components/header-controls.html`: the Projects link.

Rules:

- No column on an upstream table; studio tables point at upstream rows by id.
- No other upstream file is edited, upstream's locale files and workflows included.
- Studio strings are looked up with `st()` / `studio_t()`, never `t()`:
  upstream's tests read every `t('...')` call as a key of upstream's locale files.

## Branches, releases, upstream

- `master` is upstream's branch and is never committed to.
- `main` is the fork: an upstream release tag plus the add-on.
- Only upstream release tags are merged into `main`:

  ```bash
  git fetch upstream --tags
  git merge vX.Y.Z
  ```

- A fork release is tagged `v<upstream version>-studio.<n>`, for example
  `v0.11.0-studio.1`. The tag starts `studio-publish.yml`, which publishes
  `ghcr.io/<owner>/speakr-studio:<version>` and
  `ghcr.io/<owner>/speakr-whisper-arc:<version>` from GitHub-hosted runners.
