"""Diagnostic-only planeswalker probes; never add modified controls to candidates.

Replace unknown subtype lists with a known subtype to isolate vocabulary failures,
then inspect selected individual abilities. These controls change card semantics;
their acceptance is not acceptance of the original cards.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from assay_custom_cards import parser_checks
from data_utils import digest, write_jsonl
from mtgish_workflow import ROOT
from report_custom_collection import checked_cases
from validate_mtgish import MtgishValidator


def compact(result):
    diagnostic = result.get("parse_diagnostic") or {}
    return {"status": result["status"], "parse_complete": result["parse_complete"],
            "diagnostic": {k: diagnostic[k] for k in ("stage", "line", "column", "token") if k in diagnostic}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.output.exists() or args.workers < 1:
        parser.error("Output must be new and workers positive")
    rows, parent = checked_cases(args.normalized)
    with MtgishValidator() as validator:
        if validator.provenance != parent["parser"]:
            parser.error("Parser differs from collection")
    grammar = (ROOT / "grammars/english_grammar-type_line.json5").read_text()
    block = re.search(r'"\{\{Liliana\}\}":\s*\{([^}]+)\}', grammar).group(1)
    known = set(re.findall(r'"([^"\n]+)":', block))
    walkers = [r for r in rows if "Planeswalker" in r["draft"]["type_line"].split("—")[0].split()]
    prepared = []
    for row in walkers:
        draft = row["draft"]
        subtypes = draft["type_line"].partition("—")[2].split()
        unknown = [s for s in subtypes if s not in known]
        control = {**draft, "type_line": draft["type_line"].partition("—")[0].strip() + " — Jace"} if unknown else draft
        prepared.append((row, unknown, control))
    results = list(parser_checks((d for _, unknown, d in prepared if unknown), args.workers))
    result_iter = iter(results)
    probes = []
    for row, unknown, control in prepared:
        result = next(result_iter)[0] if unknown else row["validation"]
        probes.append({"id": row["id"], "name": row["draft"]["name"], "unknown_subtypes": unknown,
                       "original_draft": row["original_draft"], "normalized_draft": row["draft"],
                       "baseline": row["validation"], "control_draft": control,
                       "control_validation": result, "diagnostic_only": True})
    selected_names = {"Koth, One True Hero", "Tamiyo, Lunar Arcanist", "Elspeth, Protector of the Meek",
                      "Vetas of Distant Groves", "Irbek, Crocodile Hunter", "Iroshi, Blade of the Old Ways"}
    recovered = [p for p in probes if p["unknown_subtypes"] and p["control_validation"]["parse_complete"]]
    selected_names.update(p["name"] for p in recovered[:2])
    selected = [p for p in probes if p["name"] in selected_names]
    tasks = []
    for probe in selected:
        # Independently isolate the subtype using a universally supported ability.
        for label, draft in (("original_type_simple_ability", probe["normalized_draft"]),
                             ("known_type_simple_ability", probe["control_draft"])):
            tasks.append({"id": probe["id"], "name": probe["name"], "kind": label,
                          "draft": {**draft, "oracle_text": "[+1]: Draw a card."}})
        for index, line in enumerate(probe["control_draft"]["oracle_text"].splitlines()):
            if re.match(r'^\[[+−-]?[0-9X]+\]:', line):
                tasks.append({"id": probe["id"], "name": probe["name"], "kind": "isolated_loyalty_ability",
                              "line_index": index, "draft": {**probe["control_draft"], "oracle_text": line}})
    isolated = [{**task, "validation": result, "seconds": seconds, "diagnostic_only": True}
                for task, (result, seconds) in zip(tasks, parser_checks((t["draft"] for t in tasks), args.workers))]
    args.output.mkdir(parents=True)
    summary = {"kind": "diagnostic_planeswalker_controls", "final_test": False,
               "script_sha256": digest(Path(__file__).read_bytes()), "parser": parent["parser"],
               "parent_cases_sha256": parent["cases_sha256"], "workers": args.workers,
               "planeswalkers": len(walkers), "grammar_known_subtypes": len(known),
               "cards_with_unknown_subtypes": sum(bool(p["unknown_subtypes"]) for p in probes),
               "original_accepted": sum(p["baseline"]["parse_complete"] for p in probes),
               "known_type_control_accepted": sum(p["control_validation"]["parse_complete"] for p in probes),
               "unknown_subtype_control_recovered": len(recovered),
               "control_statuses": dict(Counter(p["control_validation"]["status"] for p in probes)),
               "selected": [{"id": p["id"], "name": p["name"], "unknown_subtypes": p["unknown_subtypes"],
                             "baseline": compact(p["baseline"]), "type_control": compact(p["control_validation"]),
                             "ability_probes": [{"kind": t["kind"], "line_index": t.get("line_index"), **compact(t["validation"])}
                                                for t in isolated if t["id"] == p["id"]]} for p in selected],
               "all_controls": write_jsonl(args.output / "controls.jsonl", probes),
               "isolated_abilities": write_jsonl(args.output / "isolated-abilities.jsonl", isolated),
               "interpretation": "Known-subtype substitution and ability deletion are diagnostic controls, not approved repairs. They are excluded from collection acceptance counts and training candidates. Farthest-match diagnostics identify progress, not guaranteed root causes."}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("planeswalkers", "cards_with_unknown_subtypes", "original_accepted", "known_type_control_accepted", "control_statuses")}), flush=True)


if __name__ == "__main__":
    main()
