"""Sample MSEM custom designs and measure unchanged whole-card mtgish parsing.

Source cards/results stay in the requested artifact directory, not the code repo.
This is a source feasibility assay, not a model evaluation or training release.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import random
import re
import time
import unicodedata

from data_utils import digest, without_reminder, write_jsonl
from validate_mtgish import MtgishValidator


SINGLE_FACE_LAYOUTS = {"normal", "saga", "leveler", "class"}


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    return " ".join(text.translate(str.maketrans({"’": "'", "—": "-", "−": "-"})).split())


def mechanical_key(card):
    """Conservative overlap key; never used to rewrite the parser input."""
    text = card.get("oracle_text", "")
    name = card.get("name", "")
    if name:
        text = text.replace(name, "CARDNAME")
    try:
        text = without_reminder(text)
    except ValueError:
        pass
    return tuple(normalize(text if key == "oracle_text" else card.get(key, ""))
                 for key in ("type_line", "mana_cost", "oracle_text", "power", "toughness", "loyalty"))


def official_index(path):
    names, mechanics = set(), set()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            card = json.loads(line)
            names.add(normalize(card["name"]))
            for face in card.get("card_faces") or [card]:
                names.add(normalize(face["name"]))
                mechanics.add(mechanical_key(face))
    return names, mechanics


def convert_card(card, code):
    draft = {"name": card["name"], "layout": card["layout"], "set": code.lower(),
             "type_line": card["type"], "oracle_text": card.get("text", ""),
             "mana_cost": card.get("manaCost", ""), "colors": card.get("colors", []),
             "rarity": card.get("rarity", "")}
    for field in ("power", "toughness", "loyalty", "defense"):
        if card.get(field) is not None:
            draft[field] = str(card[field])
    # Color identity includes rules symbols; only colors can indicate this face.
    # Explicit face colors matter for colored zero-cost cards and Devoid cards.
    if card.get("colorIndicator"):
        draft["color_indicator"] = card["colorIndicator"]
    return draft


def inventory(data, names, mechanics):
    counts = Counter()
    excluded_layouts = Counter()
    by_key = {}
    known_names = names | {normalize(c["name"]) for s in data["data"].values() for c in s["cards"]}
    for set_code, card_set in sorted(data["data"].items()):
        for card in card_set["cards"]:
            counts["source_records"] += 1
            if card.get("layout") not in SINGLE_FACE_LAYOUTS:
                counts["excluded_layout_records"] += 1
                excluded_layouts[card.get("layout", "missing")] += 1
                continue
            draft = convert_card(card, set_code)
            # Some printings put _SET or (SL123) in the exported name while the
            # rules still refer to the undecorated name. Only remove these when
            # the base name is independently present in this source or Oracle.
            base_name = re.sub(r"(?:_" + re.escape(set_code) + r"| \(SL\d+\))$", "", draft["name"])
            adjustments = []
            if base_name != draft["name"] and normalize(base_name) in known_names:
                adjustments.append({"field": "name", "before": draft["name"], "after": base_name,
                                    "reason": "Export printing suffix; base name independently present in source or official reference."})
                draft["name"] = base_name
                counts["normalized_printing_names"] += 1
            if normalize(draft["name"]) in names:
                counts["excluded_official_name_records"] += 1
                continue
            key = mechanical_key(draft)
            if key in mechanics:
                counts["excluded_official_mechanical_match_records"] += 1
                continue
            origin = {"set_code": set_code, "set_name": card_set.get("name"),
                      "source_uuid": card.get("uuid"), "designer": card.get("designer"),
                      "name": card["name"], "legalities": card.get("legalities", {}),
                      "import_adjustments": adjustments}
            if key in by_key:
                counts["collapsed_custom_mechanical_duplicates"] += 1
                by_key[key]["sources"].append(origin)
                continue
            by_key[key] = {"id": digest(key), "draft": draft, "sources": [origin]}
    rows = sorted(by_key.values(), key=lambda row: row["id"])
    counts["eligible_unique_designs"] = len(rows)
    return rows, {"counts": dict(counts), "excluded_layouts": dict(excluded_layouts)}


def card_type(draft):
    front = draft["type_line"].split("—")[0].split(" - ")[0]
    return " ".join(x for x in front.split() if x not in {"Legendary", "Snow", "Basic", "World"})


def color_group(draft):
    colors = draft.get("colors", [])
    return "colorless" if not colors else colors[0] if len(colors) == 1 else "multicolor"


def aggregate(rows):
    accepted = [r for r in rows if r["validation"].get("parse_complete") is True
                and r["validation"].get("status") == "parsed"]
    return {"sampled": len(rows), "accepted": len(accepted),
            "acceptance_rate": len(accepted) / len(rows) if rows else None,
            "statuses": dict(Counter(r["validation"]["status"] for r in rows)),
            "accepted_with_rewrites": sum(bool(r["validation"].get("applied_rewrites")) for r in accepted),
            "accepted_with_rules_text": sum(bool(r["draft"].get("oracle_text", "").strip()) for r in accepted)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True, help="Downloaded MSEM AllSets.json")
    p.add_argument("--official", type=Path, required=True, help="Scryfall Oracle JSONL[.gz]")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--sample-size", type=int, default=500)
    p.add_argument("--seed", type=int, default=20261009)
    a = p.parse_args()
    if a.output.exists():
        p.error("Output directory must be new")
    if a.sample_size < 1:
        p.error("Sample size must be positive")
    source_bytes = a.source.read_bytes()
    names, mechanics = official_index(a.official)
    eligible, summary = inventory(json.loads(source_bytes), names, mechanics)
    sample = random.Random(a.seed).sample(eligible, min(a.sample_size, len(eligible)))
    a.output.mkdir(parents=True)
    summary.update({"kind": "custom_source_parser_feasibility", "final_test": False,
                    "source_url": "https://mse-modern.com/msem2/notlackey/AllSets.json",
                    "source_sha256": digest(source_bytes), "official_sha256": digest(a.official.read_bytes()),
                    "script_sha256": digest(Path(__file__).read_bytes()), "seed": a.seed,
                    "sampling": "Uniform random sample without replacement from mechanically deduplicated eligible single-face designs; no filtering on keywords or parser results.",
                    "scope": "normal/saga/leveler/class single-face layouts only; multiface records excluded before sampling",
                    "overlap_policy": "Remove _SET/(SL123) printing suffixes only when the base name is independently present in source/Oracle; retain original names and adjustments. Exclude official names and normalized type/cost/text/stats matches against official faces. Deduplicate custom mechanical matches ignoring full self-name and reminder text. This does not detect every functional reprint or wording variant. Rules text is never rewritten for the assay.",
                    "interpretation": "Parser acceptance only; does not establish rules correctness, balance, request fidelity, or training/redistribution permission.",
                    "measured_at": datetime.now(timezone.utc).isoformat(),
                    "sample": write_jsonl(a.output / "sample.jsonl", sample)})
    (a.output / "inventory.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["counts"]), flush=True)
    results = []
    started = time.monotonic()
    with MtgishValidator() as validator, (a.output / "cases.jsonl").open("w") as stream:
        summary["parser"] = validator.provenance
        for item in sample:
            tick = time.monotonic()
            row = {**item, "validation": validator.check(item["draft"]),
                   "seconds": round(time.monotonic() - tick, 4)}
            results.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            if len(results) % 25 == 0:
                print(json.dumps({"completed": len(results), **aggregate(results)}), flush=True)
    summary.update(aggregate(results))
    summary["seconds"] = round(time.monotonic() - started, 2)
    for label, key in (("type", card_type), ("color", color_group), ("layout", lambda d: d["layout"])):
        groups = defaultdict(list)
        for row in results:
            groups[key(row["draft"])].append(row)
        summary["by_" + label] = {k: aggregate(v) for k, v in sorted(groups.items())}
    accepted = [r for r in results if r["validation"].get("status") == "parsed"
                and r["validation"].get("parse_complete") is True]
    summary["accepted_candidates"] = write_jsonl(a.output / "accepted-candidates.jsonl", accepted)
    summary["cases_sha256"] = digest((a.output / "cases.jsonl").read_bytes())
    (a.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("sampled", "accepted", "acceptance_rate", "statuses", "seconds")}), flush=True)


if __name__ == "__main__":
    main()
