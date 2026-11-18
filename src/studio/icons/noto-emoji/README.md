# The clapperboard

ScriptBot's icons (`src/studio/icons/*.png`, `favicon.ico`) are drawn by
`scripts/studio_icons.py` from one image of Noto Emoji, the clapperboard
(U+1F3AC):

- file: `2D/png/512/emoji_u1f3ac.png` of https://github.com/googlefonts/noto-emoji,
  release tag `v2026-09-24-unicode18_0` (commit `e20cbc2bbec1`), copied here as
  `emoji_u1f3ac.png` (SHA-256 `dd9adba6c60a338fc6f85e154acec4b7f8b775ff4946831295093818b9ee72d6`);
- licence: the repository's `LICENSE` at that tag, copied here unchanged:
  the SIL Open Font License, version 1.1, Copyright 2013 Google LLC. (The
  repository's README still says that "most image resources" are under the
  Apache License 2.0, but links that sentence to the same OFL file.)
- the Apache License 2.0 is here too, as `LICENSE-APACHE-2.0` (the text of
  https://www.apache.org/licenses/LICENSE-2.0.txt). Under either reading, the
  copyright line, the licence text and the statement of what was changed (the
  image scaled to each size and given a thin white outline, below) are all in
  this folder.

`scripts/studio_icons.py` scales the image down and gives it a thin white outline,
so that the dark board still shows on a dark tab or top bar.
