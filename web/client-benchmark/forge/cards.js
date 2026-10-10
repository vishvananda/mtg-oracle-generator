export const artStyle = 'Highly detailed fantasy painting in a wide landscape composition. Painterly realism, expressive brushwork, intricate material textures, dramatic lighting, atmospheric depth, and rich, carefully balanced colors. A single cohesive scene fills the image edge to edge.';
export function artPrompt(description, draft) {
  // The image model sees visual subject matter, not the card JSON or Oracle text.
  const subject = draft.type_line?.split(/ [—–] /).at(-1) || '';
  return `${description.trim()}${subject ? ` The central subject is a ${subject.toLowerCase()}.` : ''} ${artStyle}`.slice(0, 4000);
}
export const demos = [
  { id:'demo-dragon', name:'Vesper, Eclipse Sovereign', mana_cost:'{3}{U}{B}{R}', type_line:'Legendary Creature — Dragon', colors:['U','B','R'], rarity:'mythic', power:'5', toughness:'5', oracle_text:'Flying, menace\nWhenever Vesper deals combat damage to a player, exile the top two cards of that player\'s library. Until the end of your next turn, you may play those cards, and you may spend mana as though it were mana of any type to cast those spells.', image:'dusk-wing.webp', description:'An ancient dragon that steals forgotten spells from its enemies.', art_prompt:'A regal black dragon with subtle gold scales glides through storm clouds over a ruined gothic city at dusk. Violet lightning illuminates the towers below.' },
  { id:'demo-scholar', name:'Neris, Keeper of Lost Tides', mana_cost:'{2}{U}{U}', type_line:'Legendary Planeswalker — Neris', colors:['U'], rarity:'mythic', loyalty:'4', oracle_text:'+1: Scry 2.\n−2: Return target nonland permanent to its owner\'s hand.\n−7: You get an emblem with "Whenever you draw a card, create a token that\'s a copy of target creature you control."', image:'tide-archivist.webp', description:'A merfolk planeswalker who reshapes reality from the memories of a sunken library.', art_prompt:'A merfolk scholar in flowing blue robes levitates a luminous pearl inside a flooded ancient library. Luminous fish weave between coral-encrusted shelves.' },
  { id:'demo-stag', name:'Crownroot, Spring Eternal', mana_cost:'{3}{G}{G}', type_line:'Legendary Creature — Elk Spirit', colors:['G'], rarity:'mythic', power:'4', toughness:'6', oracle_text:'Vigilance\nWhenever another nontoken creature you control dies, create a 1/1 green Saproling creature token.\nTap five untapped creatures you control: Return target permanent card from your graveyard to your hand.', image:'grove-guardian.webp', description:'An ancient stag spirit that turns the fallen into new life and awakens the forest to reclaim forgotten relics.', art_prompt:'An enormous ancient stag with branching antlers covered in white flowers protects a moss-covered doorway in an enchanted forest. Morning light, floating pollen, roots and ferns.' },
  { id:'demo-observatory', name:'Observatory Beyond the Veil', mana_cost:'', type_line:'Legendary Land', colors:[], rarity:'rare', oracle_text:'This land enters tapped unless you control a legendary creature.\n{T}: Add {U} or {R}.\n{2}{U}{R}, {T}: Copy target activated or triggered ability you control. You may choose new targets for the copy.', image:'observatory.webp', description:'A forgotten observatory that catches the echo of a spell in a distant constellation.', art_prompt:'An ancient open-air observatory on a mountain terrace at twilight, overlooking a misty valley and a distant ruined tower. A crescent moon, deep blue clouds, warm embers.' },
  { id:'demo-giant', name:'Rhaz, Emberbound Artisan', mana_cost:'{2}{R}{R}', type_line:'Legendary Creature — Giant Artificer', colors:['R'], rarity:'rare', power:'4', toughness:'4', oracle_text:'Whenever you discard a card, create a Treasure token.\n{R}, Sacrifice an artifact: Exile the top card of your library. You may play that card this turn.', image:'ember-giant.webp', description:'A giant artificer who turns discarded ideas into treasure and burns his inventions for flashes of inspiration.', art_prompt:'A towering red-skinned giant blacksmith hammers a white-hot blade on a volcanic anvil. Sparks curl into the smoky twilight, black basalt mountains behind him.' },
].map(d => ({...d, demo:true, artist:'MTG CardForge', art_prompt:`${d.art_prompt} ${artStyle}`, illustration:new URL(`./assets/${d.image}`,import.meta.url).href }));

export function parseDraft(text) {
  const clean=text.trim().replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,'');
  let draft;
  try { draft=JSON.parse(clean); } catch { throw new Error('The model returned an incomplete card. Try generating again or simplify the description.'); }
  if (!draft || Array.isArray(draft) || typeof draft !== 'object') throw new Error('The model did not return a card object. Try again.');
  if (draft.card_faces?.length > 1) throw new Error('This first Forge supports single-face cards. Try describing a single face.');
  if (draft.card_faces?.length === 1) draft={...draft,...draft.card_faces[0]};
  if (typeof draft.name !== 'string' || !draft.name.trim() || typeof draft.type_line !== 'string' || !draft.type_line.trim() || typeof draft.oracle_text !== 'string') throw new Error('The model left out the card name, type, or rules. Try generating again.');
  return {name:draft.name.slice(0,140), type_line:draft.type_line.slice(0,180), oracle_text:draft.oracle_text.slice(0,5000),
    mana_cost:String(draft.mana_cost??'').slice(0,100), colors:Array.isArray(draft.colors)?draft.colors.filter(c=>'WUBRG'.includes(c)&&c.length===1):[],
    rarity:['common','uncommon','rare','mythic','special','bonus'].includes(draft.rarity)?draft.rarity:'rare',
    ...Object.fromEntries(['power','toughness','loyalty'].filter(k=>draft[k]!=null).map(k=>[k,String(draft[k]).slice(0,20)]))};
}
export function faceFor(card) {
  return {name:card.name, manaSymbols:card.mana_cost?.match(/\{([^}]+)\}/g)?.map(s=>s.slice(1,-1))||[],
    typeLine:card.type_line, colors:card.colors, rarity:card.rarity,
    rulesText:(card.oracle_text||'').replaceAll('CARDNAME',card.name), power:card.power??null, toughness:card.toughness??null, loyalty:card.loyalty??null,
    illustration:card.illustration||null, artist:card.artist??'MTG CardForge', setCode:card.set_code||'FORGE', frameStyle:['modern','retro'].includes(card.frame_style)?card.frame_style:undefined, borderColor:['black','white'].includes(card.border_color)?card.border_color:undefined};
}
