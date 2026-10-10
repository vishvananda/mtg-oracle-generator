"""Versioned surface checks; semantic fidelity is assessed independently."""
import re

VERSION = 'description-checks-v3'


def words(text):
    return ' '.join(re.findall(r'\w+', text.casefold()))


def mentioned(word, description):
    # Plurals and terminal punctuation do not change a requested type.
    word = word.strip(',.;')
    variants = {word}
    if word == 'Stickers': variants.add('Sticker')
    elif word.endswith('y'): variants.add(word[:-1]+'ies')
    elif word.endswith('s'): variants.add(word+'es')
    else: variants.add(word+'s')
    return any(re.search(r'(?<!\w)'+re.escape(v)+r'(?!\w)', description, re.I) for v in variants)


def deterministic_checks(projection, description):
    issues = []
    costs = re.findall(r'(?:\{[0-9A-Z/]+\})+', description)
    def check_fields(target):
        faces = target.get('card_faces', [])
        # A parent's joined cost/type is display metadata. Check individual faces;
        # the independent reviewer checks which face each characteristic belongs to.
        if faces:
            for face in faces: check_fields(face)
            return
        if target.get('mana_cost') and target['mana_cost'] not in costs:
            issues.append('missing_or_changed_mana_cost:'+target['mana_cost'])
        if target.get('power') is not None and f"{target['power']}/{target['toughness']}" not in description.replace(' ', ''):
            issues.append('missing_or_changed_stats')
        typ = target.get('type_line', '')
        subtypes = typ.split(' — ', 1)[1].split() if ' — ' in typ else []
        creature_implied = bool(re.search(r'\b\d+\s*/\s*\d+\b', description)) or (
            'Creature' in typ and bool(subtypes) and all(mentioned(w, description) for w in subtypes))
        for word in re.split(r'\s+|—|//', typ):
            if word == 'Creature' and creature_implied: continue
            if word == 'Creature' and re.search(r'\b(?:flyer|flier)s?\b', description, re.I): continue
            if word == 'Planeswalker' and mentioned('walker', description): continue
            if word == 'Artifact' and re.search(r'\bmana rocks?\b', description, re.I): continue
            if word == 'Artifact' and 'Equipment' in subtypes and mentioned('Equipment', description): continue
            if word == 'Enchantment' and any(w in subtypes and mentioned(w, description)
                                           for w in ('Aura', 'Saga', 'Class', 'Case', 'Room', 'Shrine', 'Curse', 'Background')): continue
            if word and not mentioned(word, description): issues.append('missing_type_word:'+word)
    check_fields(projection['target'])
    if projection['style'] in ('minimal', 'concept'):
        target = projection['target']
        if 'mana_cost' not in target and re.search(r'\{[^}]+\}', description): issues.append('detail_level_leaked_mana_cost')
        if 'power' not in target and re.search(r'\b\d+\s*/\s*\d+\b', description): issues.append('detail_level_leaked_exact_stats')
    if len(description) > 12000 or not description.strip(): issues.append('invalid_description_length')
    return sorted(set(issues))


def source_name_issues(source, projection, description):
    name = source['name']
    needle = words(name)
    if len(name) <= 4 or not needle or (' '+needle+' ') not in (' '+words(description)+' '): return []
    # Token names and explicit names in mechanics are legitimate vocabulary.
    # Ignore name fields themselves, which would make this check tautological.
    target = projection.get('reference_draft') or projection['target']
    color_names = dict(zip('WUBRG', ('white','blue','black','red','green')))
    def vocabulary(value):
        result = set(words(value.get('type_line', '')).split())
        result.update(color_names[c] for c in value.get('colors', []) if c in color_names)
        for face in value.get('card_faces', []): result.update(vocabulary(face))
        return result
    # "Red Dragon", "Artifact Zombie", and "Human // Wolf" are ordinary
    # characteristic descriptions even when the source uses them as its name.
    if set(needle.split()) <= vocabulary(target): return []
    def allowed(value):
        mechanic_alias = needle == 'regeneration' and bool(re.search(r'\bregenerate\b', value.get('oracle_text', ''), re.I))
        return mechanic_alias or any((' '+needle+' ') in (' '+words(value.get(k, ''))+' ')
                   for k in ('type_line', 'oracle_text')) or any(allowed(f) for f in value.get('card_faces', []))
    return [] if allowed(target) else ['source_name_leak']


def row_checks(row, source):
    projection = {'style': row['style'], 'target': row['expressed_constraints']}
    if row.get('description_policy') == 'plausible_completion': projection['reference_draft'] = row['target']
    return deterministic_checks(projection, row['description']) + source_name_issues(source, projection, row['description'])
