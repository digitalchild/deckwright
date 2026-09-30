"""Compare a generated pack with a reference pack, layout by layout.

Matches layouts by (source, ref) and reports: layouts found, same targets,
same kind, and the differences.

Run: uv run python scripts/compare_packs.py <reference pack.json> <pptx> [-v]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from deckwright.generator import generate
from deckwright.pack import LayoutSpec, Pack


def targets(l: LayoutSpec) -> dict[str, list[int] | int]:
    out: dict[str, list[int] | int] = {f"f:{f.name}": f.target for f in l.fields}
    if l.items:
        out |= {f"i:{k}": v for k, v in l.items.targets.items()}
        out |= {f"x:{k}": v for k, v in l.items.extra.items()}
    return out


def compare(ref: Pack, gen: Pack) -> dict:
    r = {(l.source, l.ref): l for l in ref.layouts}
    g = {(l.source, l.ref): l for l in gen.layouts}
    rows = []
    for key, rl in r.items():
        gl = g.get(key)
        if gl is None:
            rows.append({"ref": rl.id, "key": key, "found": False})
            continue
        rt, gt = targets(rl), targets(gl)
        rset = {v for x in rt.values() for v in (x if isinstance(x, list) else [x])}
        gset = {v for x in gt.values() for v in (x if isinstance(x, list) else [x])}
        rows.append({"ref": rl.id, "gen": gl.id, "key": key, "found": True, "kind": rl.kind == gl.kind or
                     rl.kind in gl.aliases or gl.kind in rl.aliases, "ref_kind": rl.kind, "gen_kind": gl.kind,
                     "targets": rset == gset, "missing": sorted(rset - gset), "extra": sorted(gset - rset),
                     "ref_t": rt, "gen_t": gt})
    found = [x for x in rows if x["found"]]
    return {
        "reference": len(r), "generated": len(g), "found": len(found),
        "not_in_reference": sorted(set(g) - set(r)),
        "kind_ok": sum(x["kind"] for x in found), "targets_ok": sum(x["targets"] for x in found),
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("reference", type=Path)
    ap.add_argument("pptx", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    ref = Pack.model_validate_json(a.reference.read_text())
    res = compare(ref, generate(a.pptx, ref.id))
    for x in res["rows"]:
        if not x["found"]:
            print(f"MISSING  {x['ref']:<24} {x['key']}")
        elif not (x["kind"] and x["targets"]) or a.verbose:
            print(f"{'ok' if x['kind'] and x['targets'] else 'DIFF':<8} {x['ref']:<24} -> {x['gen']:<16} "
                  f"kind {x['ref_kind']}/{x['gen_kind']} missing {x['missing']} extra {x['extra']}")
            if a.verbose and not x["targets"]:
                print("         ref", json.dumps(x["ref_t"]))
                print("         gen", json.dumps(x["gen_t"]))
    print(f"found {res['found']}/{res['reference']}, kind {res['kind_ok']}/{res['found']}, "
          f"targets {res['targets_ok']}/{res['found']}, extra layouts {res['not_in_reference']}")


if __name__ == "__main__":
    main()
