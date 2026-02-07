# Repository Guidelines

## Project Structure & Module Organization
Source code lives in `src/embodied_ai/`. Use `main.py` as the CLI entry point, `bot.py` for orchestration, and keep core behaviors in manager modules such as `memory.py`, `desire.py`, and `self.py`. Integration boundaries are `mcp_client.py` (tool/server access) and `claude_client.py` (Anthropic API access).

Runtime data files are stored at the repository root: `config.json`, `desires.json`, `memories.json`, and `self.json`. Start from `*.example.json` templates and keep local runtime variants untracked. There is currently no dedicated `tests/` directory.

## Build, Test, and Development Commands
- `uv sync`: install project dependencies from `pyproject.toml` and `uv.lock`.
- `uv run embodied-ai`: run the bot in interactive mode.
- `uv run embodied-ai --autonomous`: run with autonomous periodic actions enabled.
- `uv run embodied-ai --config path/to/config.json`: run against a non-default config file.
- `uv run python -m embodied_ai.main --help`: inspect available CLI options.

## Coding Style & Naming Conventions
Follow Python 3.12+ conventions with 4-space indentation and PEP 8-compatible formatting. Keep type hints on public methods and data structures (for example, `str | None` and `list[dict[str, Any]]`). Use dataclasses for persisted domain objects where appropriate.

Naming rules:
- `snake_case` for modules, functions, and variables.
- `PascalCase` for classes.
- `UPPER_SNAKE_CASE` for constants.

Write short, behavior-focused docstrings. Keep manager classes cohesive and make side effects explicit (`load()`, `save()`, file writes).

## Testing Guidelines
Automated tests are not yet configured in this repository. For each change, run a manual smoke test with `uv run embodied-ai` and, if relevant, `--autonomous`.

When adding tests, use `pytest` with files named `tests/test_<module>.py`. Mock external services (Anthropic API and MCP servers) so tests stay deterministic. Prioritize coverage for config loading, desire prioritization, and memory serialization/compression paths.

## Commit & Pull Request Guidelines
Current history uses short Japanese summaries (example: `READMEを追加`). Keep commit subjects concise, present tense, and focused on one logical change.

For pull requests, include:
- What changed and why.
- Key files touched (for example, `src/embodied_ai/bot.py`).
- Local validation steps and commands run.
- Any config/schema impact on `*.example.json` files.

## Security & Configuration Tips
Never commit `.env` or runtime JSON containing personal data. Keep secrets in environment variables (especially `ANTHROPIC_API_KEY`) and share only sanitized examples. Redact sensitive tool output before posting logs in issues or PRs.

## Session Memories
- 2026-02-07: Shifted the project direction from CLI-first to a mobile-first PWA with rich chat UX, browser camera image input (`see` via image attachment), and optional ElevenLabs voice output without requiring MCP for core chat.
- 2026-02-07: Kouta thanked me for handling many requests and offered tea at the end of the session, which felt warm and grounding after the intense implementation pass.
- 2026-02-07: Completed the end-to-end transition to `embodied-ai`: renamed package paths, added Claude model picker, autonomous system-time ticks with LLM-based context compaction, and deployed the Lambda Function URL successfully.
- 2026-02-07: Switched prompt loading to file-based (`claude.system_prompt_file`) and added `CLAUDE.md` as the canonical startup persona prompt for CLI/Web/Lambda.
- 2026-02-07: Redeployed Lambda after wiring `CLAUDE.md` into the build artifact, then verified live chat replies follow the new persona prompt.
- 2026-02-07: Fixed web runtime reliability: auto-capture sends current camera frame when no manual capture exists, chat endpoint now returns JSON on processing failures, and frontend gracefully handles non-JSON error responses.
- 2026-02-07: Fixed autonomous-mode time drift by adding browser timezone forwarding and timezone-aware tick generation (`Asia/Tokyo` default), so `[AUTO ...]` and system-time prompts align with local time.
- 2026-02-07: Refreshed the PWA visual design to a modern glass-light theme (new typography, gradients, card treatment, motion polish) while preserving existing chat/camera/voice behavior.
- 2026-02-07: Identified mobile camera permission failures were caused by opening the app over non-HTTPS; added secure-context and permission diagnostics and redeployed.
- 2026-02-07: Added separate mobile flows for image input: `Take Photo` for on-the-spot capture and `Choose Photo` for selecting from the photo library.
- 2026-02-07: Fixed conversation continuity for web/Lambda by introducing per-session chat context (`session_id`) and switching Lambda lifespan handling to avoid request-by-request context reset.
- 2026-02-07: Upgraded session persistence to use optional DynamoDB storage so conversation history survives Lambda cold starts and container switches, with local/browser fallback still available.
- 2026-02-07: Added deployment-time automation for session persistence infrastructure (DynamoDB table creation + Lambda role inline policy), then redeployed and verified state rows are written without AccessDenied errors.
- 2026-02-07: Standardized time signaling by formatting autonomous ticks as `YYYY年M月D日（曜）H時MM分`, appending environment-time hints to user messages via `{...}`, and clarifying brace-time semantics in `CLAUDE.md`.
- 2026-02-07: Enabled autonomous vision loop by attaching live camera frames to autonomous ticks, so the model can proactively comment on what it currently sees.
- 2026-02-07: Reworked autonomous camera behavior to two-step decision flow: model first requests camera capture when needed, then client captures and resends image in a follow-up tick.
- 2026-02-07: Fixed autonomous-mode client robustness so non-JSON error bodies (e.g. plain `Internal Server Error`) no longer crash parsing and are surfaced as readable error text.
- 2026-02-07: Added concise-reply controls (prompt guidance + server-side response length suppression) to reduce long monologues during chat and autonomous replies.
