# Issue tracker: Local Markdown

- One feature per directory: `.scratch/<feature-slug>/`.
- Feature spec: `.scratch/<feature-slug>/spec.md`.
- One implementation ticket per file:
  `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`.
- Record triage state in a `Status:` line near the top, using
  the strings in `triage-labels.md`.
- Append conversation under `## Comments`.

When a skill says "publish to the issue tracker", create the appropriate
local file. When it says "fetch the relevant ticket", read that file.

If a ticket number matches multiple features, ask which feature.
