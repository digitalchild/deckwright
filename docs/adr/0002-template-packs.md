# ADR 0002: Template packs

Date: 2026-09-30
Status: accepted

## Context

ADR 0001 fixed the engine design to one hard-coded catalog (`catalog.py`) for one template file. That catalog held 61 hand-mapped layouts, with shape ids, placeholder indexes and example text tied to that one `.pptx`. Brand colours, fonts and rules were also hard-coded in `brand.py`.

Deckwright needs to work with any `.pptx` template, not one. A new template must not need a person to write a catalog by hand. Shape ids and placeholder indexes are tedious and error-prone to map, and they change every time the template changes.

## Decision

1. **Data-only, machine-owned packs.** A template pack is a folder with `template.pptx` and `pack.json`. `pack.json` holds the layouts, fields, items, brand and handler geometry. It is validated against pydantic models (`src/deckwright/pack.py`) and a JSON Schema generated from them (`schemas/pack.schema.json`). Tools write `pack.json`. People never edit it by hand.
2. **A heuristic generator, not an LLM.** `deckwright template add` inspects a `.pptx` with `python-pptx` and `lxml`: shapes, groups, placeholders, text, fonts, colours and images. It finds text and image slots, groups repeating shapes into item series, guesses a content kind for each design, and reads the brand from the theme. The tool calls no LLM API and needs no network.
3. **Agent review through MCP.** The generator builds a sample deck, one slide per layout, and a report of warnings, low-confidence kinds and unmapped shapes. A person or an agent reviews the sample deck and sends small patches (`update_pack` / `deckwright template patch`) to fix real problems. The pack only changes when the review finds one.
4. **A sample pack ships in the wheel.** `src/deckwright/templates/sample/` is a neutral, generated template used by tests, examples and this documentation. It needs no external file to try Deckwright.
5. **Private packs stay out of the repo.** A pack built from a real organisation's own template (for example a conference deck template) lives in `~/.config/deckwright/templates/<id>/`, picked up by pack discovery. It is never checked into this repository.

## Consequences

- Acceptance for the generator is measured against a hand-made reference pack: it must find at least 95% of the reference layouts, and get the kind and the field or item targets right for about 95% of the layouts it finds. The rest surface as issues for review, not silent mistakes.
- A person adding a template first runs `template add`, then reviews the sample deck, then confirms. This costs a few minutes instead of an afternoon of hand-mapping shape ids.
- The generator cannot know things the template does not show. Two designs that differ only in colour, and not in shape layout, may be mapped to the same layout. A design with no sample text (a master layout only) gets fields named by size and role, which is less precise than a designer's own naming.
- `pack.json` changing the pack format needs a schema version bump and a migration path; today there is only `schema_version: 1`.
- The engine, selector, service, API, MCP server and CLI now all take a `Template` object built from a pack, instead of importing one global catalog. Adding a second, third or tenth template needs no code change.
