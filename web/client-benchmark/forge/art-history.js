const keys=['art_blob','art_prompt','art_settings','artist','demo_image','art_source_url'];
/** One previous successful image; no nested history or persisted blob URLs. */
export function rememberArtwork(card){
  if(!card.illustration)return;
  card.previous_art=Object.fromEntries(keys.filter(k=>k in card).map(k=>[k,card[k]]));
  if(!card.art_blob&&!card.demo_image)card.previous_art.illustration=card.illustration;
}
export function restoreArtwork(card){
  const previous=card.previous_art;if(!previous)return false;
  const url=previous.art_blob?URL.createObjectURL(previous.art_blob):previous.demo_image?new URL(`./assets/${previous.demo_image}`,import.meta.url).href:previous.illustration;
  if(!url)return false;
  if(card.illustration?.startsWith('blob:'))URL.revokeObjectURL(card.illustration);
  for(const key of keys)delete card[key];
  Object.assign(card,previous,{illustration:url});delete card.previous_art;
  return true;
}
