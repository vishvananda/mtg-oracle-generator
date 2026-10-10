"""Remove official-card duplicates from parsed custom cards using mtgish structure.

Only the top-level card name is ignored. Named-card references elsewhere in the
rules remain significant. This is an additional conservative overlap check, not
a proof that remaining cards have novel mechanics.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

from assay_custom_cards import normalize, official_index
from data_utils import digest, write_jsonl


CARD = re.compile(r'^(Card\(Name: )("(?:[^"\\]|\\.)*")(,.*)$')


def canonical_parser_key(text):
    match = CARD.fullmatch(text.strip())
    if not match:
        return None
    return match[1] + "<SELF>" + match[3]


def filter_candidates(accepted, reference, deduplicate_custom=False):
    retained, overlaps, duplicates = [], [], []
    seen = {}
    for row in accepted:
        key = canonical_parser_key(row["validation"]["mtgish"])
        matches = reference.get(key, []) if key is not None else []
        if matches:
            overlaps.append({"id": row["id"], "name": row["draft"]["name"], "official_matches": matches})
            continue
        if deduplicate_custom and key is not None:
            equivalent = {"id": row["id"], "name": row["draft"]["name"], "sources": row["sources"]}
            if key in seen:
                representative = seen[key]
                representative["equivalent_designs"].append(equivalent)
                duplicates.append({"id": row["id"], "name": row["draft"]["name"],
                                   "representative_id": representative["id"]})
                continue
            row = {**row, "design_family_id": digest(key), "equivalent_designs": [equivalent]}
            seen[key] = row
        retained.append(row)
    return retained, overlaps, duplicates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--official-parsed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deduplicate-custom", action="store_true", help="Also collapse exact parsed custom duplicates after wording cleanup")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output directory must be new")
    parent = json.loads(args.cases.with_name("summary.json").read_text())
    if parent["cases_sha256"] != digest(args.cases.read_bytes()):
        parser.error("Parent cases hash mismatch")
    names, _ = official_index(args.official)
    reference = defaultdict(list)
    for line in args.official_parsed.read_text().splitlines():
        match = CARD.fullmatch(line.strip())
        if match and normalize(json.loads(match[2])) in names:
            reference[canonical_parser_key(line)].append(json.loads(match[2]))
    rows = [json.loads(line) for line in args.cases.read_text().splitlines()]
    accepted = [r for r in rows if r["validation"]["status"] == "parsed" and r["validation"].get("parse_complete") is True]
    retained, overlaps, duplicates = filter_candidates(accepted, reference, args.deduplicate_custom)
    with_rules = [r for r in retained if r["draft"]["oracle_text"].strip()]
    args.output.mkdir(parents=True)
    summary = {"kind": "post_normalization_official_overlap_filter", "final_test": False,
               "parent_cases_sha256": parent["cases_sha256"],
               "script_sha256": digest(Path(__file__).read_bytes()),
               "official_sha256": digest(args.official.read_bytes()),
               "official_parsed_sha256": digest(args.official_parsed.read_bytes()),
               "official_parsed_structures": len(reference), "accepted_before_filter": len(accepted),
               "exact_parsed_overlaps": overlaps, "retained": len(retained), "retained_with_rules": len(with_rules),
               "deduplicate_custom": args.deduplicate_custom, "exact_parsed_custom_duplicates": duplicates,
               "candidates": write_jsonl(args.output / "candidates.jsonl", with_rules),
               "policy": "Exact mtgish single-card structure equality after replacing only the top-level card name. Remaining designs are review candidates; incomplete overlap detection and parser acceptance do not prove novelty, correctness, or training rights."}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
