"""Keep editable defaults coherent when sparse descriptions omit source abilities."""
import copy
import re


def sparse_defaults(target, expressed):
    """Repair only unrequested fields; preserve explicit costs/colors and mechanics."""
    if target.get('oracle_text') or target.get('card_faces') or target.get('colors')!=[]:
        return target,[]
    cost=target.get('mana_cost','')
    colors=[c for c in 'WUBRG' if c in cost]
    if not colors: return target,[]
    result=copy.deepcopy(target)
    if 'colors' not in expressed:
        result['colors']=colors
        changes=[{'field':'colors','before':[],'after':colors,
                  'reason':'With color-changing rules omitted, infer suggested colors from the suggested mana cost.'}]
    elif 'mana_cost' not in expressed:
        # Preserve the suggested mana value when fixed; X-dependent source costs
        # get a fixed editable default after their defining mechanics were omitted.
        symbols=re.findall(r'\{([^}]+)\}',cost)
        value=3 if any(s in ('X','Y','Z') for s in symbols) else sum(
            max([int(p) for p in s.split('/') if p.isdigit()] or [1]) for s in symbols)
        result['mana_cost']='{'+str(value)+'}'
        changes=[{'field':'mana_cost','before':cost,'after':result['mana_cost'],
                  'reason':'Honor explicitly requested colorlessness with a colorless suggested cost when source rules were omitted.'}]
    else: return target,[]
    return result,changes


def coherent_record(row):
    if row.get('style') not in ('minimal','concept') or 'expressed_constraints' not in row: return row
    target,changes=sparse_defaults(row['target'],row['expressed_constraints'])
    if not changes: return row
    return {**row,'target':target,'completion_adjustments':changes,'target_before_completion_adjustment':row['target']}
