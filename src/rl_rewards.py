"""Offline reward prototype. No training or model calls occur on import.

Parser acceptance is a small auxiliary signal behind intent gates. Unknown
judge results are ineligible, not neutral positive rewards. Embedding similarity
is deliberately absent from the reward.
"""
import json
from jsonschema import Draft202012Validator
from data_utils import digest
from paths import PROJECT

VERSION='intent-gated-oracle-v1'


def parsed(result):
    return result.get('status')=='parsed' and result.get('parse_complete') is True


def inspect(case,raw,schema,parser,hit_token_limit=False):
    report={'reward_version':VERSION,'case_id':case['id'],'raw_sha256':digest(raw),
            'eligible':True,'schema_valid':False,'deterministic_violations':[]}
    if hit_token_limit:
        return {**report,'reward':-1.,'gate':'truncated_generation'}
    try:
        card=json.loads(raw)
        errors=sorted(e.message for e in Draft202012Validator(schema).iter_errors(card))
    except (ValueError,TypeError):return {**report,'reward':-1.,'gate':'invalid_json'}
    if errors:return {**report,'reward':-1.,'gate':'invalid_schema','schema_errors':errors}
    report['schema_valid']=True
    issues=report['deterministic_violations']
    for field,expected in case.get('explicit_metadata',{}).items():
        actual=card.get(field)
        # An explicitly named front face can coexist with the conventional
        # "Front // Back" root name. Never accept a match on the back face.
        if field=='name' and card.get('card_faces') and card['card_faces'][0].get('name')==expected:
            continue
        if field not in card:
            issues.append('explicit_'+field+'_missing');continue
        if field=='colors':actual,expected=set(actual or []),set(expected)
        if actual!=expected:issues.append('explicit_'+field+'_changed')
    if not card.get('name','').strip():issues.append('missing_name')
    for face in card.get('card_faces') or [card]:
        if not face.get('name','').strip() and card.get('card_faces'):issues.append('missing_face_name')
        types=face.get('type_line','').split('—')[0].casefold().split()
        for kind,fields in [('creature',('power','toughness')),('planeswalker',('loyalty',)),('battle',('defense',))]:
            if kind in types:
                issues.extend('missing_'+f for f in fields if not str(face.get(f,'')).strip())
    report['mtgish']=parser(card)
    report['has_oracle_text']=any(f['oracle_text'].strip() for f in card.get('card_faces') or [card])
    if issues:return {**report,'reward':-.8,'gate':'explicit_metadata_or_required_characteristic'}
    return {**report,'reward':None,'gate':'needs_intent_judge'}


def combine(report,judgment,reference_parse=None):
    if report.get('reward') is not None:return report
    if judgment is None or judgment.get('confidence')!='high' or judgment.get('verdict')=='uncertain':
        return {**report,'eligible':False,'reward':None,'gate':'judge_abstention','judgment':judgment}
    verdict=judgment.get('verdict');style=judgment.get('oracle_style');violations=judgment.get('violations')
    if verdict not in ('pass','fail') or type(style) is not int or style not in (0,1,2) or not isinstance(violations,list):
        raise ValueError('Invalid judge result')
    if verdict=='pass' and violations:
        return {**report,'eligible':False,'reward':None,'gate':'judge_inconsistent',
                'judgment':judgment,'judge_error':'Passing judgment contains violations'}
    if verdict=='fail':return {**report,'reward':-.6,'gate':'intent_failure','judgment':judgment}
    if style==0:return {**report,'reward':-.5,'gate':'unusable_rules','judgment':judgment}
    status=report['mtgish'].get('status')
    if status=='parser_error' or (reference_parse or {}).get('status')=='parser_error':
        return {**report,'eligible':False,'reward':None,'gate':'parser_infrastructure_failure','judgment':judgment}
    # Unsupported reference grammar is not evidence that faithful output is bad.
    # Missing/unsupported reference => neutral parser term, never a syntax penalty.
    syntax=(1. if parsed(report['mtgish']) else 0.) if reference_parse and parsed(reference_parse) else .5
    reward=.75+.15*(style/2)+.10*syntax
    return {**report,'reward':round(reward,6),'gate':'faithful_candidate','judgment':judgment,
            'components':{'intent':.75,'oracle_style':.15*(style/2),'parser':.10*syntax},
            'reference_parser_supported':bool(reference_parse and parsed(reference_parse))}


def judge_schema():
    review={'type':'object','additionalProperties':False,'required':['id','verdict','confidence','oracle_style','violations'],
        'properties':{'id':{'type':'string'},'verdict':{'enum':['pass','fail','uncertain']},
            'confidence':{'enum':['high','medium','low']},'oracle_style':{'type':'integer','enum':[0,1,2]},
            'violations':{'type':'array','items':{'type':'string'}}}}
    return {'type':'object','additionalProperties':False,'required':['reviews'],
            'properties':{'reviews':{'anyOf':[{'type':'null'},{'type':'array','items':review}]}}}


def judge_payload(cases):
    # Gold labels, parser verdicts and generator identity are never sent to the judge.
    return {'instructions':(PROJECT/'prompts/reward-judge-v1.txt').read_text(),
            'cases':[{k:c[k] for k in ('id','request','candidate','explicit_metadata','references') if k in c} for c in cases]}


def align_judgments(value,cases):
    values=value.get('reviews')
    ids=[c['id'] for c in cases]
    if not isinstance(values,list) or len(values)!=len(ids) or {v['id'] for v in values}!=set(ids):
        raise ValueError('Missing, duplicate or unknown judge IDs')
    return {v['id']:v for v in values}


def group_is_usable(reports):
    """Fail closed for uncertain groups; never train by ranking only bad outputs."""
    if not reports or any(not r.get('eligible') or r.get('reward') is None for r in reports):
        return False
    return (any(r['gate']=='faithful_candidate' for r in reports)
            and len({r['reward'] for r in reports})>1)
