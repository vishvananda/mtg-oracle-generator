"""Recheck a frozen custom-card sample after explicit wording normalization.

Unknown keywords, subtypes, ability words, restrictions, and reminder text remain present. Both versions
and both parser outcomes are retained. No model calls or grammar changes.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import time

from assay_custom_cards import aggregate, parser_checks
from data_utils import digest, write_jsonl
from validate_mtgish import MtgishValidator


def modernize_enters(draft):
    result = copy.deepcopy(draft)
    changes = []
    for index, face in enumerate(result.get("card_faces") or [result]):
        old = face.get("oracle_text", "")
        new, count = re.subn(r"\b([Ee]nters?) the battlefield\b", r"\1", old)
        if count:
            face["oracle_text"] = new
            changes.append({"face_index": index, "field": "oracle_text", "replacements": count,
                            "before": old, "after": new})
    return result, changes


def normalize_wording(draft, rules):
    result = copy.deepcopy(draft)
    changes = []
    for rule in rules:
        for index, face in enumerate(result.get("card_faces") or [result]):
            old = face.get("oracle_text", "")
            new = old
            if rule == "enters":
                new = re.sub(r"\b([Ee]nters?) the battlefield\b", r"\1", old)
            elif rule == "quotes":
                new = old.translate(str.maketrans({"“": '"', "”": '"'}))
            elif rule == "loyalty-minus":
                new = re.sub(r"(?m)^(\[?)-(?=[0-9X]+\]?:)", r"\1−", old)
            elif rule == "renowned-self" and "Creature" in face.get("type_line", "").split("—")[0].split():
                # Deliberately narrow: a named permanent's own static condition,
                # outside quotes, not a global name substitution across zones.
                prefix = "As long as " + face.get("name", "") + " is renowned,"
                lines, quote_depth = [], 0
                for line in old.split("\n"):
                    lines.append(line.replace(prefix, "As long as this creature is renowned,", 1)
                                 if quote_depth % 2 == 0 and line.startswith(prefix) else line)
                    quote_depth += sum(line.count(mark) for mark in ('"', '“', '”'))
                new = "\n".join(lines)
            elif rule == "sacrifice-choice":
                # Foundations' nonfunctional clarification of who chooses.
                # Explicit player subjects and a single unrestricted object;
                # leave sacrifice costs and constrained selections untouched.
                new = re.sub(r"\b((?:[Ee]ach (?:player|opponent)|[Tt]arget (?:player|opponent)|[Tt]hat player) sacrifices (?:a (?:nonland permanent|creature or planeswalker|creature|permanent|land)|an (?:artifact|enchantment)))(?=[.,])",
                             r"\1 of their choice", old)
            elif rule == "owner-choice":
                # The owner already makes this choice. Never apply to a bare
                # 'Put target ...' instruction, where the effect's controller chooses.
                new = re.sub(r"((?:[Tt]he owner of target (?:nonland permanent|permanent|creature)|[Ii]ts owner) puts it on) the top or bottom of their library",
                             r"\1 their choice of the top or bottom of their library", old)
            if new != old:
                face["oracle_text"] = new
                changes.append({"rule": rule, "face_index": index, "field": "oracle_text", "before": old, "after": new})
    return result, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--rules", nargs="+", choices=("enters", "quotes", "loyalty-minus", "renowned-self", "sacrifice-choice", "owner-choice"), default=["enters"])
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("Workers must be positive")
    if args.output.exists():
        parser.error("Output directory must be new")
    parent_path = args.cases.with_name("summary.json")
    parent = json.loads(parent_path.read_text())
    if digest(args.cases.read_bytes()) != parent["cases_sha256"]:
        parser.error("Cases do not match parent summary")
    rows = [json.loads(line) for line in args.cases.read_text().splitlines()]
    if len(rows) != parent["sampled"] or len({r["id"] for r in rows}) != len(rows):
        parser.error("Sample size or uniqueness mismatch")
    result_rows = []
    started = time.monotonic()
    with MtgishValidator() as validator:
        if validator.provenance != parent["parser"]:
            parser.error("Parser changed since baseline assay")
        args.output.mkdir(parents=True)
        prepared = [(row, *normalize_wording(row["draft"], args.rules)) for row in rows]
        checks = parser_checks((draft for _, draft, changes in prepared if changes), args.workers)
        with (args.output / "cases.jsonl").open("w") as stream:
            for row, draft, changes in prepared:
                result, seconds = next(checks) if changes else (row["validation"], 0.0)
                updated = {**row, "original_draft": row["draft"], "raw_validation": row["validation"],
                           "draft": draft, "normalization_changes": changes, "validation": result,
                           "raw_seconds": row.get("seconds"), "seconds": seconds,
                           "reused_raw_validation": not bool(changes)}
                result_rows.append(updated)
                stream.write(json.dumps(updated, ensure_ascii=False) + "\n")
                stream.flush()
                if len(result_rows) % 1000 == 0:
                    print(json.dumps({"completed": len(result_rows), "total": len(rows),
                                      "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)
        # Consume the generator's finalization so all worker clients are closed.
        if next(checks, None) is not None:
            raise RuntimeError("Unexpected extra parser result")
    changed = [r for r in result_rows if r["normalization_changes"]]
    gained = [r for r in changed if r["raw_validation"]["status"] != "parsed" and r["validation"]["status"] == "parsed"]
    lost = [r for r in changed if r["raw_validation"]["status"] == "parsed" and r["validation"]["status"] != "parsed"]
    accepted = [r for r in result_rows if r["validation"].get("parse_complete") is True and r["validation"]["status"] == "parsed"]
    summary = {"kind": "historical_wording_normalization_assay", "final_test": False,
               "rules": args.rules, "workers": args.workers,
               "parent_cases_sha256": parent["cases_sha256"], "parent_summary_sha256": digest(parent_path.read_bytes()),
               "script_sha256": digest(Path(__file__).read_bytes()), "parser": parent["parser"],
               "baseline": aggregate(rows), "modernized": aggregate(result_rows),
               "changed_cards": len(changed), "newly_accepted": len(gained), "lost_acceptances": len(lost),
               "changed_case_ids": [r["id"] for r in changed],
               "newly_accepted_names": [r["draft"]["name"] for r in gained],
               "lost_acceptance_names": [r["draft"]["name"] for r in lost],
               "seconds": round(time.monotonic() - started, 2),
               "interpretation": "Same frozen sample and unchanged parser; only the listed wording/typography normalizations, with exact diffs retained. Not a rules-correctness or model-accuracy measurement.",
               "accepted_candidates": write_jsonl(args.output / "accepted-candidates.jsonl", accepted),
               "accepted_with_rules_candidates": write_jsonl(args.output / "accepted-with-rules.jsonl", [r for r in accepted if r["draft"]["oracle_text"].strip()]),
               "cases_sha256": digest((args.output / "cases.jsonl").read_bytes())}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("baseline", "modernized", "changed_cards", "newly_accepted", "lost_acceptances", "seconds")}), flush=True)


if __name__ == "__main__":
    main()
