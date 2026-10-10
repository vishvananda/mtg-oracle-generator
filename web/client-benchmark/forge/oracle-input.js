// Single-face subset of upstream preprocess_scryfall.cardInfo. The Forge does
// not yet author multi-face layouts; report them as unsupported, never flatten.
export function oracleInput(card){
 if(card.card_faces?.length>1||!['normal','single',undefined,null,''].includes(card.layout))throw Error('This browser check currently supports single-face cards.');
 if(typeof card.name!=='string'||!card.name.trim()||typeof card.type_line!=='string'||!card.type_line.trim()||typeof card.oracle_text!=='string')throw Error('Name, type and Oracle text are required for validation.');
 if(card.oracle_text.length>16000||card.name.length>200||card.type_line.length>300)throw Error('This card is too long to validate.');
 const face={name:card.name,type:card.type_line,text:card.oracle_text.replaceAll('CARDNAME',card.name).replace(/^([+−-]?[0-9X]+):/gm,'[$1]:')};
 for(const [source,target]of [['mana_cost','manaCost'],['power','power'],['toughness','toughness'],['loyalty','loyalty'],['defense','defense'],['life_modifier','life'],['hand_modifier','hand']])if(card[source]!=null&&card[source]!=='')face[target]=String(card[source]);
 if(card.color_indicator?.length)face.colorIndicator=[...'WUBRG'].filter(c=>card.color_indicator.includes(c)).join('');
 return {entry:{name:face.name,layout:'single',cards:[face]}};
}
