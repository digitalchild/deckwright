# Kinds

A kind is a content shape, not a specific design. Every template pack maps its own layouts to these kinds. Write a deck spec slide with `"kind": "<kind>"` and the layout selector picks the best matching layout in the active pack. Give `"layout": "<id>"` instead to pick one layout by id directly.

Field names below are the names the generator uses when it maps a template. A pack may add its own layouts with the same names. `items` fields are per-item; `number` fills itself and needs no content.

| Kind | Use it for | Fields | Item fields | Sample pack layout |
|---|---|---|---|---|
| `title` (alias `cover`) | First slide of a deck. | `title`, `subtitle`, `presenter`, `date`, `image` | - | `title` |
| `section` | Start a new section of the talk. | `label`, `title`, `subtitle` | - | `section`, `section-header` |
| `statement` | A key message or transition. | `title`, `subtitle` | - | `statement` |
| `agenda` | Outline of the talk, short items. | `title` | `number` (auto), `label` | `agenda` |
| `steps` (alias of `agenda`) | Next steps or a short procedure. | `title` | `number` (auto), `label` | `agenda` |
| `text` | A single idea explained in a short paragraph. | `title`, `body` | - | `text`, `title-content` |
| `points` (alias, covers `rows`/`cards`/`columns`) | A few points that each need a sentence. | `title` | `title`, `body` | `rows-3`, `cards-3` |
| `rows` | Stacked rows, one sentence each. | `title` | `title`, `body` | `rows-3` |
| `cards` | A fixed number of short takeaways in cards. | `title` | `title`, `body` | `cards-3` |
| `columns` | Parallel points side by side. | `title` | `title`, `body` | - |
| `text-image` | A point supported by one or more images. | `title`, `body`, `image` | `image`, `caption` | `text-image` |
| `image` (alias also covers `cover`-style image slides) | Photos or screenshots, image-led. | `title`, `caption`, `image` | `image` | `images-2`, `image-caption` |
| `stat` | One key metric. | `value`, `label` | - | `big-number` |
| `stats` | Two or more supporting metrics. | `title` | `value`, `label` | `stats-3` |
| `chart` | Compare a few percentages as shape-based bars or columns. | `title`, `subtitle` | `value`, `label` | `bar-chart` |
| `quote` | A customer or expert quote. | `quote`, `attribution` | - | `quote` |
| `timeline` | Dated milestones or phases. | `title` | `label`, `body`, `date` | `timeline` |
| `code` | A short code or JSON snippet. | `title`, `subtitle`, `filename`, `code` | - | `code` |
| `closing` | Last slide. | `title` | - | `closing` |
| `qa` | Question time slide. | `title` | - | - |
| `divider` | A visual pause, artwork only. Use sparingly. | - | - | - |

## Standard field names

The generator names slots with these shared names so a deck spec written for one pack's `stat` layout also works with another pack's `stat` layout:

- Scalar fields: `title`, `subtitle`, `body`, `body2`, `label`, `value`, `caption`, `date`, `presenter`, `quote`, `attribution`, `code`, `filename`, `image`, `image2`.
- Item fields: `number` (auto-generated, `01`, `02`, ...), `title`, `body`, `label`, `value`, `image`, `caption`.

## Notes

- `caption` pairs with an `image`. `label` pairs with a `value` (a `stat`/`stats` slide) or stands alone as a short tag (a `section` slide, or an `agenda` item).
- `subtitle` and `body` are both secondary text under a `title`; `subtitle` is short (one line), `body` is a paragraph.
- A pack is not required to implement every kind. `deckwright layouts --template <id>` or the MCP `list_layouts` tool lists what a pack actually has. Building a spec with a kind the pack has no layout for fails with a clear error naming the missing kind.
- See [docs/packs.md](packs.md) for how the generator decides field names and kinds when it reads a new `.pptx`.
