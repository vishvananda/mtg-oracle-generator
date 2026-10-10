"""Audit a completed custom-card collection and export coverage/review queues.

No semantic approval or training split is inferred from parser acceptance.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from assay_custom_cards import aggregate, card_type, color_group
from data_utils import digest, write_jsonl


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def checked_cases(directory):
    summary = json.loads((directory / "summary.json").read_text())
    path = directory / "cases.jsonl"
    if summary["cases_sha256"] != digest(path.read_bytes()):
        raise ValueError(f"Cases hash mismatch: {directory}")
    return read_rows(path), summary


def coverage(rows):
    groups = {
        "type": card_type,
        "color": color_group,
        "color_combination": lambda d: "".join(c for c in "WUBRG" if c in d.get("colors", [])) or "colorless",
        "layout": lambda d: d["layout"],
        "rarity": lambda d: d.get("rarity", "missing"),
        "rules_lines": lambda d: str(min(4, len(d.get("oracle_text", "").strip().splitlines()))) + ("+" if len(d.get("oracle_text", "").strip().splitlines()) >= 4 else ""),
    }
    sources = [source for r in rows for design in r.get("equivalent_designs", [r]) for source in design["sources"]]
    return {"cards": len(rows), "source_sets": len({s["set_code"] for s in sources}),
            **{"by_" + label: dict(sorted(Counter(key(r["draft"]) for r in rows).items()))
               for label, key in groups.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--pilot-baseline", type=Path)
    parser.add_argument("--pilot-normalized", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output directory must be new")
    raw, raw_summary = checked_cases(args.baseline)
    normalized, norm_summary = checked_cases(args.normalized)
    candidate_summary = json.loads((args.candidates / "summary.json").read_text())
    path = args.candidates / "candidates.jsonl"
    if digest(path.read_bytes()) != candidate_summary["candidates"]["sha256"]:
        raise ValueError("Candidates hash mismatch")
    candidates = read_rows(path)
    if len({r["id"] for r in raw}) != len(raw) or [r["id"] for r in raw] != [r["id"] for r in normalized]:
        raise ValueError("Raw/normalized identities or ordering changed")
    if raw_summary["parser"] != norm_summary["parser"]:
        raise ValueError("Parser provenance changed")
    if norm_summary["parent_cases_sha256"] != raw_summary["cases_sha256"] or candidate_summary["parent_cases_sha256"] != norm_summary["cases_sha256"]:
        raise ValueError("Collection stages do not share the same parent")
    for before, after in zip(raw, normalized):
        if before["draft"] != after["original_draft"] or before["validation"] != after["raw_validation"]:
            raise ValueError("Original card or parser outcome was not preserved")
    by_id = {r["id"]: r for r in normalized}
    if len({r["id"] for r in candidates}) != len(candidates):
        raise ValueError("Duplicate candidate ID")
    for row in candidates:
        original = by_id[row["id"]]
        if row["draft"] != original["draft"] or row["validation"] != original["validation"] or not row["validation"]["parse_complete"]:
            raise ValueError("Candidate does not match a complete parse")
    comparisons = {}
    for label, pilot_path, full in (("baseline", args.pilot_baseline, raw), ("normalized", args.pilot_normalized, normalized)):
        if pilot_path:
            pilot, _ = checked_cases(pilot_path)
            index = {r["id"]: r for r in full}
            changed = [r["id"] for r in pilot if r["id"] not in index or any(r[k] != index[r["id"]][k] for k in ("draft", "validation"))]
            comparisons[label] = {"pilot_cards": len(pilot), "identical_drafts_and_results": len(pilot) - len(changed), "changed_ids": changed}
    groups = defaultdict(list)
    for row in normalized:
        groups[card_type(row["draft"])].append(row)
    accepted = [r for r in normalized if r["validation"].get("parse_complete") is True and r["validation"]["status"] == "parsed"]
    rejected = [r for r in normalized if not (r["validation"].get("parse_complete") is True and r["validation"]["status"] == "parsed")]
    recovered = [r for r in normalized if r["raw_validation"]["status"] != "parsed" and r["validation"]["status"] == "parsed"]
    lost = [r for r in normalized if r["raw_validation"]["status"] == "parsed" and r["validation"]["status"] != "parsed"]
    args.output.mkdir(parents=True)
    summary = {
        "kind": "custom_collection_coverage", "final_test": False, "semantic_review": "pending",
        "script_sha256": digest(Path(__file__).read_bytes()),
        "baseline_cases_sha256": raw_summary["cases_sha256"], "normalized_cases_sha256": norm_summary["cases_sha256"],
        "candidate_sha256": candidate_summary["candidates"]["sha256"],
        "baseline": aggregate(raw), "normalized": aggregate(normalized),
        "coverage_eligible": coverage(raw), "coverage_accepted": coverage(accepted), "coverage_candidates": coverage(candidates),
        "normalized_by_type": {key: aggregate(value) for key, value in sorted(groups.items())},
        "new_acceptances": len(recovered), "lost_acceptances": len(lost),
        "recovered_by_rule_combination": dict(Counter(" + ".join(c["rule"] for c in r["normalization_changes"]) for r in recovered)),
        "pilot_recheck": comparisons,
        "rejected_for_review": write_jsonl(args.output / "rejected-for-review.jsonl", rejected),
        "recovered_for_review": write_jsonl(args.output / "recovered-for-review.jsonl", recovered),
        "lost_acceptances_for_review": write_jsonl(args.output / "lost-acceptances-for-review.jsonl", lost),
        "interpretation": "Coverage and complete parser acceptance only. Candidates await semantic review and description generation. Rejections include parser limitations; they are not labels of invalid Magic rules. Rule-combination counts record changed rules, not isolated causal attribution.",
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
