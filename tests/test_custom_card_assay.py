import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assay_custom_cards import convert_card, inventory, mechanical_key
from assay_custom_wording import modernize_enters, normalize_wording
from filter_custom_candidates import canonical_parser_key


def source_card(name="Custom Test", **changes):
    return {"name": name, "layout": "normal", "type": "Creature — Giant",
            "manaCost": "{3}{R}", "text": "{R}: Custom Test gets +1/+0 until end of turn.",
            "power": "3", "toughness": "3", "colors": ["R"], "rarity": "common", **changes}


class CustomCardAssayTests(unittest.TestCase):
    def test_conversion_preserves_unknown_rules_and_loyalty(self):
        text = "+1: Dreamweave. (Create a new kind of counter.)\n−4: Draw three cards."
        card = source_card(type="Legendary Planeswalker — Example", text=text, loyalty="4")
        result = convert_card(card, "TEST")
        self.assertEqual(result["oracle_text"], text)
        self.assertEqual(result["loyalty"], "4")

    def test_renamed_duplicates_collapse_but_changed_stats_do_not(self):
        a = convert_card(source_card(), "TEST")
        b = {**a, "name": "Different Name", "oracle_text": a["oracle_text"].replace(a["name"], "Different Name")}
        self.assertEqual(mechanical_key(a), mechanical_key(b))
        self.assertNotEqual(mechanical_key(a), mechanical_key({**b, "power": "4"}))

    def test_official_overlap_and_multiface_exclusions_are_counted(self):
        official = source_card("Official Name")
        renamed_official = source_card("Renamed", text="Flying (This creature has flying.)")
        unique = source_card()
        duplicate = source_card("Duplicate", text=unique["text"].replace("Custom Test", "Duplicate"))
        paired = source_card("Front // Back", layout="transform")
        data = {"data": {"TEST": {"name": "Test Set", "cards": [official, renamed_official, unique, duplicate, paired]}}}
        signature = mechanical_key(convert_card({**renamed_official, "text": "Flying"}, "TEST"))
        rows, report = inventory(data, {"official name"}, {signature})
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["sources"]), 2)
        self.assertEqual(report["counts"], {"source_records": 5, "excluded_official_name_records": 1,
                         "excluded_official_mechanical_match_records": 1, "collapsed_custom_mechanical_duplicates": 1,
                         "excluded_layout_records": 1, "eligible_unique_designs": 1})

    def test_printing_suffix_cannot_hide_official_reprint(self):
        card = source_card("Thornweald Archer_TEST", text="Reach, deathtouch")
        data = {"data": {"TEST": {"cards": [card]}}}
        rows, report = inventory(data, {"thornweald archer"}, set())
        self.assertEqual(rows, [])
        self.assertEqual(report["counts"]["excluded_official_name_records"], 1)

    def test_printing_alias_collapses_without_changing_rules(self):
        base = source_card()
        printing = source_card("Custom Test (SL123)")
        data = {"data": {"LAIR": {"cards": [printing, base]}}}
        rows, _ = inventory(data, set(), set())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["draft"]["name"], "Custom Test")
        self.assertEqual(rows[0]["draft"]["oracle_text"], base["text"])
        self.assertEqual(rows[0]["sources"][0]["import_adjustments"][0]["before"], "Custom Test (SL123)")

    def test_historical_enters_cleanup_preserves_unknown_mechanics_and_original(self):
        text = "When this creature enters the battlefield, dreamweave.\nCreatures enter the battlefield tapped.\nWhenever a creature leaves the battlefield, draw a card."
        draft = {"name": "Test", "oracle_text": text}
        result, changes = modernize_enters(draft)
        self.assertEqual(draft["oracle_text"], text)
        self.assertEqual(result["oracle_text"], "When this creature enters, dreamweave.\nCreatures enter tapped.\nWhenever a creature leaves the battlefield, draw a card.")
        self.assertEqual(changes[0]["replacements"], 2)

    def test_already_modern_wording_is_unchanged(self):
        draft = {"oracle_text": "This creature enters tapped.\nEngorge (A custom mechanic.)"}
        result, changes = modernize_enters(draft)
        self.assertEqual(result, draft)
        self.assertEqual(changes, [])

    def test_quotes_and_loyalty_only_change_typography(self):
        draft = {"oracle_text": '[-2]: Target creature gets -2/-2.\n[+1]: It gains “{T}: Draw a card.”'}
        result, changes = normalize_wording(draft, ["quotes", "loyalty-minus"])
        self.assertEqual(result["oracle_text"], '[−2]: Target creature gets -2/-2.\n[+1]: It gains "{T}: Draw a card."')
        self.assertEqual([c["rule"] for c in changes], ["quotes", "loyalty-minus"])

    def test_self_reference_rewrite_is_narrow_and_does_not_touch_quoted_text(self):
        draft = {"name": "Example", "type_line": "Creature — Human", "oracle_text": 'As long as Example is renowned, it has trample.\nOther creatures have "As long as Example is renowned, draw a card."'}
        result, _ = normalize_wording(draft, ["renowned-self"])
        self.assertTrue(result["oracle_text"].startswith('As long as this creature is renowned,'))
        self.assertIn('Other creatures have "As long as Example is renowned,', result["oracle_text"])

    def test_multiline_granted_ability_is_not_a_self_reference(self):
        draft = {"name": "Example", "type_line": "Creature", "oracle_text": 'Other creatures have "Flying\nAs long as Example is renowned, this creature has trample."'}
        self.assertEqual(normalize_wording(draft, ["renowned-self"]), (draft, []))

    def test_sacrifice_clarification_does_not_change_costs_or_restrictions(self):
        draft = {"oracle_text": 'Each opponent sacrifices a creature.\nSacrifice a creature: Draw a card.\nTarget opponent sacrifices a creature with flying.\nEach player sacrifices a creature of their choice.'}
        result, _ = normalize_wording(draft, ["sacrifice-choice"])
        self.assertEqual(result["oracle_text"], draft["oracle_text"].replace('Each opponent sacrifices a creature.', 'Each opponent sacrifices a creature of their choice.'))
        self.assertEqual(normalize_wording(result, ["sacrifice-choice"]), (result, []))

    def test_library_choice_preserves_the_person_choosing(self):
        draft = {"oracle_text": 'The owner of target nonland permanent puts it on the top or bottom of their library.\nPut target creature on the top or bottom of its owner\'s library.'}
        result, _ = normalize_wording(draft, ["owner-choice"])
        self.assertEqual(result["oracle_text"], draft["oracle_text"].replace('puts it on the top', 'puts it on their choice of the top'))

    def test_parser_overlap_ignores_only_the_top_level_name(self):
        first = 'Card(Name: "First", Rules: [NamedCard("Other")])'
        renamed = 'Card(Name: "Second", Rules: [NamedCard("Other")])'
        different = 'Card(Name: "Second", Rules: [NamedCard("Different")])'
        self.assertEqual(canonical_parser_key(first), canonical_parser_key(renamed))
        self.assertNotEqual(canonical_parser_key(first), canonical_parser_key(different))

    def test_parser_overlap_handles_escaped_quotes_and_declines_other_layouts(self):
        self.assertEqual(canonical_parser_key('Card(Name: "\\\"First\\\"", Rules: [])'),
                         canonical_parser_key('Card(Name: "Second", Rules: [])'))
        self.assertIsNone(canonical_parser_key('MultiCard(Name: "First", Rules: [])'))


if __name__ == "__main__":
    unittest.main()
