# ADR 0001: Template engine design

Date: 2026-09-29
Status: accepted

Superseded in part by ADR 0002 (template packs).

## Context

The source is the original conference template, a Google Slides export. It has 51 slides and 55 slide layouts on one master.

- Slides 1 to 8 are hidden guide pages (colours, fonts, type sizes, co-branding, submission rules).
- Slides 9 to 51 are the designs. They carry artwork at slide level (logo footer, photos, dotted patterns). Many text boxes are unlinked placeholders (`idx=4294967295`), so the master layouts alone do not reproduce the designs.
- The master layouts add designs that no sample slide uses (text cards, columns, detailed agenda, pink stats, label grid, Q&A, Thank you, mascot characters). They do not carry the logo footer.
- Layout names are Google export names (`CUSTOM_8_1_2_1_2_1_1_2_1`), so they carry no meaning.
- The export sets the theme font to Arial, but the brand allows only the brand font. The embedded fonts are EOT and cannot be reused for rendering.

## Decision

1. **Two sources in one catalog.** A layout either clones a sample slide (shape tree, background and relationships copied, then the originals are removed) or creates a slide from a master layout. Fields address shape ids for clones and placeholder `idx` for master layouts. Master-layout slides get the logo footer copied from slide 15.
2. **Semantic catalog in code** (`catalog.py`). Each entry has an id, a kind, usage guidance, fields, item slots with min and max, and an example. The examples double as documentation, thumbnails and tests.
3. **Formatting comes from the template.** The filler keeps each shape's paragraph and run properties and only replaces text. `**text**` reuses the pink run the designer used in that shape, or the theme `accent1` colour.
4. **Selection by scoring.** A `kind` maps to candidate layouts. The score penalises dropped fields, missing required fields, item counts out of range and missing item images. It prefers the tightest fit and penalises reuse of a design earlier in the deck.
5. **Fit by estimate.** Text height is estimated from box size, font size and character count. The template's own sample text is treated as fitting, which calibrates the estimate. Overflowing text shrinks in 5% steps down to 55% and produces a warning.
6. **Brand font enforcement.** Runs with no font take the font set elsewhere in the same box, or the brand font when the slot would fall back to Arial.
7. **Rendering** uses LibreOffice with a private profile whose font folder holds static builds of the brand font, Geist Mono, Manrope and Be Vietnam Pro files, built from the OFL Google Fonts sources.

## Consequences

- Designs stay pixel-identical to the template, including artwork that placeholders cannot express.
- Layouts whose decorations sit in the master (cards, timeline dots) need exact item counts. The catalog marks these with `min == max`.
- Charts are shapes, not native chart objects. Bar and column sizes follow the percentages, but the chart is not editable as data in PowerPoint.
- The text fit is an estimate. LibreOffice previews are the check. PowerPoint may wrap slightly differently.
- A new template version needs its shape ids and placeholder indexes re-mapped in `catalog.py`. The catalog test checks that every target exists.
