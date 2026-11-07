# Factory skill bundle

The four skills in `image/skills/` are VM deployment assets, not repository-local
agent skills. They do not participate in this repository's `.agents/skills/`
discovery; development agents can use their globally installed skills instead.

Docker and CDK staging package the same explicit bundle. The image installs it
root-owned under `/opt/factory/skills/`, with factory-user native discovery through
`/home/factory/.agents/skills/`. Canonical files are readable but not writable by
`factory`. Discovery is not authorization: worker stages explicitly select canonical
skill paths and enforce independent verification and review-before-publication.
The current slice completes with an open, unmerged PR; repair and automatic merge
remain unimplemented.

## Reference inventory

Every skill includes `SKILL.md` and `agents/openai.yaml`.

| Skill | Bundled references and dependencies |
| --- | --- |
| implement | Reads tdd; returns to the worker for independent review. |
| code-review | Covers Standards and Spec in one read-only conversation. |
| pr | Includes `CREDITS.md`; upstream show-me URLs are attribution, not executable dependencies. |
| tdd | Includes `tests.md` and `mocking.md`; links to read-only review guidance. |

Tests resolve local Markdown links within this bundle. New skill/tool references
need dependency review; do not install dependencies or silently omit supporting
files. Source staging and Docker context allow only the required skill files.

## Capability inventory

| Capability | Required by | Verification boundary |
| --- | --- | --- |
| File reading/search, shell, Git | Ordinary implementation, review, and repair | Existing image/sandbox checks; no Skill tool required. |
| Native user-skill discovery | All worker stages | Offline pinned-Codex app-server skills listing as `factory`; checks canonical resolution and non-writability. |
| Repository setup and verification tools | Implementation/repair and independent worker verification | Supplied by the target repository's executable scripts; bundling skills does not provide a language toolchain. |
| Structured review output | Fresh review | Offline preflight checks pinned CLI output-schema/last-message flags. Job lifecycle tests simulate structured results and reject invalid evidence or candidate mutation. Real provider behavior still requires separately authorized inference. |
| GitHub CLI, repository-scoped token, network | Publication and merge | Existing installed CLI and credential scope; live GitHub checks and merge policy require separately authorized verification. |
| AWS credential discovery and model access | Agent execution | Existing worker/image configuration; no model/provider change or live inference is implied by packaging. |

The ordinary path needs no tracker bootstrap, interactive approval wait, MCP
server, browser, or diagram renderer. Missing essential requirements, required
capabilities, or a new product/interface decision must return a blocked outcome.
Review remains read-only even when the managed policy allows workspace writes.

## Verification

- Focused source/asset checks: `uv run --locked python -m unittest src.tests.test_image_runtime src.tests.test_infra -v`.
- Image discovery, permissions, managed policy, and sandbox checks: `make preflight`.
- Repository checks and local synthesis: `make verify` and `make synth`.

Preflight runtime checks use offline containers and no model inference. They do not prove that
an agent follows instructions, structured review works with the provider, GitHub
branch protection is enforced, or deployed queue execution is serialized. Live
capability testing, deployment, and billable Jobs require separate authorization.
