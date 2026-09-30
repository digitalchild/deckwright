# TODO

Status on 2026-09-30. Read this first when you pick up the work.

## Current state

- Phases 1 to 7 of [plan-open-source.md](plan-open-source.md) are done: the pack model, the generic core, the sample pack, the generator, the confirmation and patch tools, the CLI, HTTP API and MCP surfaces, and Google Slides output all work against template packs.
- Phase 8 (clean-up and release) is in progress.

## 1. Open source, multi-template tool

See [plan-open-source.md](plan-open-source.md). It replaces the old section 1 (template registry, surfaces, kind contract, inspection tool, standard template).

## 2. Smaller improvements (proposals, not requested yet)

- [ ] Resolve relative image paths against the spec file's folder in the CLI.
- [ ] Optionally drop unused master layouts from output decks to reduce file size (about 0.9 MB of layout art).
- [ ] Native PowerPoint charts as an alternative to shape-based bar and column charts.
- [ ] Add a type checker (none is configured today).
- [ ] Verify decks in real PowerPoint and Keynote (only LibreOffice was used so far).
- [ ] Verify saved `.excalidraw` files open in excalidraw.com.
- [ ] Run the generator on a third, unseen template and tune the heuristics.

## Notes for the next session

- Do not install anything without asking first. This covers brew, npm, uv packages, fonts, and `uv run --with`.
- LibreOffice and poppler are installed (Homebrew) and used only for previews. Fonts are in `~/Library/Fonts` and in `~/.cache/deckwright/lo-profile`.
- Claude Desktop drops manual `claude_desktop_config.json` entries on restart. Use the `.mcpb` extension.
- For Claude Code CLI: `claude mcp add deckwright -- uv run --directory <repo path> deckwright mcp`
- Run tests: `uv run pytest -q`. Lint: `uv run ruff check src scripts tests`.
