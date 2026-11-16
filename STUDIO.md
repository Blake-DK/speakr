# The studio fork

This repository is a fork of [Speakr](https://github.com/murtaza-nasir/speakr)
with one add-on, **studio**, kept apart from upstream's code so that new
upstream releases merge cleanly. Speakr's own documentation is in
[README.md](README.md) and [docs/](docs/); everything below is about the fork.

The fork is published under the same licence as Speakr, the GNU AGPL v3
([LICENSE](LICENSE)). The Projects page links here, so the people who use a
site running this code can get its source.

## What the add-on adds

- `/studio`: the Projects page, linked from a Projects button in the top bar
  of every page. A project is a film or production: its recordings,
  materials (PDF, PowerPoint and image files) and voice references (examples
  of how it should sound) together, shared with the people its owner picks.
- On a project's page, the project's name opens the project switcher: the
  person's projects, the most recently opened first, with a filter box once
  there are more than seven. The Projects page uses the same order. When a
  person last opened each project is kept per person on the server
  (`studio_project_visit`), so every device shows the same order.
- Right after signing in, the bare address (`/`) opens the Projects page.
  With `STUDIO_LANDING_FROM_OUTSIDE=true` it also does so for a typed address,
  a bookmark or a link from another site (off by default: Speakr's own
  browser tests open `/` that way and expect the recordings page). A link to
  `/` inside the app, a reload, any other path and any query string still
  show Speakr's recordings page; the Projects page links there too ("All
  recordings"). The rule is in `src/studio/landing.py`.
- `GET /api/studio/health`: `{"status": "ok"}`, for monitoring.
- `whisper/`: a Whisper transcription service for Intel Arc (and NVIDIA) GPUs
  with an OpenAI-compatible route, which Speakr reaches through its `openasr`
  connector. See [whisper/README.md](whisper/README.md).

### How a project uses Speakr

- Each project has a Speakr folder of its own, owned by the project's owner.
  Recordings added on the project page go into it through Speakr's own upload
  route, so Speakr's queue transcribes them. The project's transcription
  hints are the folder's hotwords and initial prompt.
- Sharing a project creates a Speakr group for it (the owner as group admin,
  each person added as a member) and makes the folder that group's folder,
  so Speakr shares the folder's recordings with the group itself. This needs
  `ENABLE_INTERNAL_SHARING=true`. Removing a person ends their membership and
  takes the recordings they added out of the folder; removing the last
  person makes the project private and deletes the group.
- Who sees a project: its owner; the people on its People list who are still
  members of its group; and site admins. Everyone else gets "not found".
  Changes made to the group in Speakr's group screens can narrow that list,
  never widen it. A project lists only the recordings Speakr lets the viewer
  open. The rule is in `src/studio/access.py`.
- Materials are stored through Speakr's storage service (local disk or S3)
  under `studio/materials/`. Accepted: PDF, PPTX, PNG, JPG, WebP and GIF, up
  to 30 MB each, checked by their first bytes as well as their names.
- When Speakr deletes a folder, group, recording or account, the studio rows
  that point at it are let go of in the same transaction
  (`src/studio/cleanup.py`).

## Where it lives

| path | what |
|---|---|
| `src/studio/` | blueprint, access rule, sharing, materials, voice references, strings, database tables (`studio_*`), schema steps |
| `templates/studio/`, `static/js/studio/` | the add-on's pages |
| `static/locales-studio/` | the add-on's interface strings |
| `tests/studio/` | the add-on's tests (run with upstream's suite) |
| `whisper/` | the transcription service |
| `Dockerfile.studio` | the app image: upstream's lite image + the add-on's extras |
| `requirements-studio.txt` | the add-on's own Python packages, pinned with hashes |
| `.github/workflows/studio-publish.yml` | builds and publishes both images |

Upstream files the add-on touches, each in one marked block:

- `src/app.py`: registers the studio blueprint;
- `src/models/__init__.py`: imports `src.studio.models`;
- `templates/components/header-controls.html`: the Projects button.

Rules:

- No column on an upstream table; studio tables point at upstream rows by id.
- No other upstream file is edited, upstream's locale files and workflows included.
- Studio strings are looked up with `st()` / `studio_t()`, never `t()`:
  upstream's tests read every `t('...')` call as a key of upstream's locale files.
- `db.create_all()` creates the studio tables but never changes one that
  exists. A later change to a studio table (a column, an index) is a step in
  `src/studio/schema.py`: it runs once per database at start-up, right after
  upstream's own migrations, and is recorded in `studio_schema_step`.
- The add-on's Python packages are in `requirements-studio.txt` (installed by
  `Dockerfile.studio`). Upstream's test run does not install them, so tests
  that need one skip there; `studio-publish.yml` runs `tests/studio` inside the
  built image, where they are installed.

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
