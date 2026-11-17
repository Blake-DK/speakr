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
- "What to share": on Speakr's recorder, "System audio" and "Microphone +
  system" first show a short dialog that says what to pick in the browser's
  share dialog on this computer and browser (Entire Screen with "Share system
  audio", or a tab with its audio switch on), then the browser asks. Continue,
  Cancel (reported as a cancelled share) and "Don't show this again" (kept in
  the browser). It wraps `navigator.mediaDevices.getDisplayMedia` from
  `static/js/studio/main-page.js` and uses Speakr's own capability check
  (`static/js/modules/utils/platform.js`); the logic is in
  `static/js/studio/share-help.js`, tested by `share-help.test.js` with
  Speakr's Vitest suite.
- A project's Chats tab: chats with a language model (Claude through
  Anthropic's SDK, or GPT through OpenAI's Responses API), and eight guided
  workflows (marketing scripts, campaign bundle, investor materials,
  planning, script coverage, pitch deck, press kit, competitor comparison
  with web research). See "The chats" below.
- A project's Documents tab: answers saved from a chat and documents written
  there, which the model reads as project knowledge in every chat, each
  downloadable as Markdown, Word, PDF or PowerPoint slides. See "The
  documents" below.
- "Generate an image" on the Materials tab: poster concepts and social
  visuals from OpenAI's image API, stored as materials.
- `/studio/settings` (site admins only, linked from the Projects page): each
  provider's model, on or off, its live model list and a connection test,
  and the image model.
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
  open, and says how many others it holds and whose they are. The rule is in
  `src/studio/access.py`.
- Site admins see every project, but Speakr opens a recording only to its
  owner and the people it is shared with. With
  `STUDIO_ADMINS_SEE_RECORDINGS=true` (off by default) every project keeps a
  Speakr group and group folder, private projects included, and every site
  admin other than the owner is a member of each project's group, so Speakr
  shares the folder's recordings with them read-only and keeps that in step
  (`src/studio/admin_access.py`). The memberships it makes are recorded in
  `studio_admin_grant` and taken back exactly: when someone stops being an
  admin (after the request that changed it, or before their next request),
  and for everyone when the setting goes off (at the next start, which also
  makes a project with an empty People list private again). The owner and the
  people on the People list are told on the project page. Being in the group
  also lets an admin add recordings of their own to the project. Needs
  `ENABLE_INTERNAL_SHARING=true`; without it the rule waits.
  The rule never takes a request down. If giving an admin their memberships
  fails, the change is rolled back, the error is logged, and the request goes
  on as if the rule had not run (the next request tries again). If taking
  access back from someone who is no longer an admin fails, the change is
  rolled back, the error is logged, and that person's requests are refused
  with a plain 503 until the take-back succeeds, so a former admin never
  keeps reading because of the error. A request that needs no take-back is
  never refused.
- Materials are stored through Speakr's storage service (local disk or S3)
  under `studio/materials/`. Accepted: PDF, PPTX, PNG, JPG, WebP and GIF, up
  to 30 MB each, checked by their first bytes as well as their names.
- When Speakr deletes a folder, group, recording or account, the studio rows
  that point at it are let go of in the same transaction
  (`src/studio/cleanup.py`).

### The chats

- A chat belongs to its project (`studio_chat`, `studio_message`). Everyone
  who sees the project sees its chats and may write in them; the chat's
  maker or the project's owner renames and deletes it.
- Keys come from the environment only, never the database or a page:
  `STUDIO_ANTHROPIC_API_KEY` and `STUDIO_OPENAI_API_KEY`. A provider without
  a key shows as unavailable. Models: `STUDIO_CLAUDE_MODEL` (default
  `claude-opus-5-5`) and `STUDIO_OPENAI_MODEL`, unless an admin picks another
  on the settings page (`studio_setting`, nothing secret in it).
- An answer streams as Server-Sent Events from a generator that holds its own
  app context, as Speakr's own chat does. One answer at a time per chat:
  the chat is held while an answer is written (a second send gets 409) and
  let go when it ends, or two minutes after a worker died. Stop, or a lost
  connection, keeps what was written so far, marked "(stopped)". A provider
  error is kept in the chat (role `error`), never sent to a provider.
- A message can carry the project's materials (PDF and images as files, slide
  decks as their text) and the transcripts of its recordings that the sender
  may open, up to 20 attachments and 20 MB. When the history goes to a
  provider, each attachment is read again with the access of the person
  sending that turn; one they may not open, or that is gone, becomes a
  one-line note.
- The system prompt: the project's name, logline and notes, the workflow's
  instructions, the project's saved documents (a 16,000-character budget,
  the most recently changed first, older ones by title only) and its voice
  references the sender may open (a 12,000-character budget), each of which
  the person can leave out with the context bar. Prompts and workflows are in
  `src/studio/workflows.py`.
- With web research on, the pages an answer cites are listed under it
  ("Sources"). The web search tool with dynamic filtering (the one used,
  `web_search_20260209`) gives no citations, so when an answer cites nothing
  the pages the search found are listed instead (at most 8), under "Web
  pages the search found".
- Model answers are Markdown, shown through Speakr's own safe renderer
  (`static/js/markdown-safe.js`), with images dropped and links opened in a
  new tab.

### The documents

- A document belongs to its project (`studio_document`): a title and Markdown
  text, typed in on the Documents tab or saved from a chat answer with "Save
  as document" (the answer's text without a "(stopped)" mark; the document
  takes the chat's kind, a plain chat or the workflow it came from).
  Everyone who sees the project reads its documents, makes new ones and
  changes any of them; the document's maker or the project's owner deletes
  it. Documents go with their project; a deleted account's documents in
  other people's projects stay, with no name on them.
- Two people can have a document open at once. A save names the version it
  was opened at (`updated_at`); when the document changed in between, the
  save is refused (409), the message names who changed it, and the editor
  keeps the person's text.
- Downloads (`src/studio/export.py`): Markdown is the text as saved; Word goes
  through upstream's own converter (`src/services/document.py`); PDF through
  WeasyPrint (the Markdown as HTML, cleaned with `bleach` to an allow-list,
  rendered with a URL fetcher that refuses every address, `file:` included;
  the fonts are the image's own DejaVu); slides through python-pptx, every
  `#` or `##` heading starting a slide. Images are taken out of the text
  before every conversion but Markdown, so no converter fetches an address
  written into a document. WeasyPrint's system libraries (pango, harfbuzz,
  fontconfig, a font) are installed by `Dockerfile.studio`.

### Generated images

- `POST /api/studio/projects/<id>/images` with a prompt, a shape
  (`1024x1536` poster, `1024x1024` square, `1536x1024` wide) and a quality
  (low, medium, high), for everyone who may add materials. The PNG is stored
  as a material through the same checks as an upload, named after the
  prompt.
- The key is `STUDIO_OPENAI_API_KEY`; image generation follows GPT's switch on
  the settings page. The model is the settings page's image model (picked
  from the key's live list of image models), else `STUDIO_OPENAI_IMAGE_MODEL`,
  else `gpt-image-2.6-flare`. The sizes and qualities are the ones every
  current GPT Image model accepts. Without a key the button says why and
  nothing is sent. The route answers with the request's token counts.

## Where it lives

| path | what |
|---|---|
| `src/studio/` | blueprint, access rule, sharing, the administrators' rule, materials, voice references, chats and their providers (`providers/`), workflows, documents and their exports, generated images, strings, database tables (`studio_*`), schema steps |
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
- `templates/components/header-controls.html`: the Projects button;
- `templates/index.html` (Speakr's main page): one line at the end of the
  body that includes `templates/studio/main_page.html`, the studio's scripts
  on that page. Upstream offers no hook for a script there: its theme slots
  are inside the Vue app, where a script tag does not run.

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
