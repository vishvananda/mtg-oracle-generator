/** DOM ranges preserve the actual card font's kerning and baseline. */
export function textRect(node,start=0,end=node?.textContent.length){
  if(!node)return null;
  const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);let offset=0,first,last;
  while(walker.nextNode()){const text=walker.currentNode,length=text.length;if(!first&&start<=offset+length)first=[text,Math.max(0,start-offset)];if(end<=offset+length){last=[text,Math.max(0,end-offset)];break;}offset+=length;}
  if(!first||!last)return node.getBoundingClientRect();const range=document.createRange();range.setStart(...first);range.setEnd(...last);return range.getBoundingClientRect();
}
export function relativeRect(rect,host){const b=host.getBoundingClientRect();return {x:rect.left-b.left,y:rect.top-b.top,width:rect.width,height:rect.height};}
export function typeWords(node){return [...(node?.textContent||'').matchAll(/\S+/g)].map(m=>({value:m[0],start:m.index,end:m.index+m[0].length,rect:textRect(node,m.index,m.index+m[0].length)}));}
export function hit(rect,point,pad=0){return rect&&point&&point.x>=rect.left-pad&&point.x<=rect.right+pad&&point.y>=rect.top-pad&&point.y<=rect.bottom+pad;}
export function ruleItems(host,text){
  const costs=[...host.querySelectorAll('.rendered-special-badge > span')],mana=[...host.querySelectorAll('.rendered-card-rules .mana-symbol,.rendered-special-text .mana-symbol')];let mi=0,ci=0;
  return [...text.matchAll(/\{([^}\n]+)\}|^([+−-]?(?:\d+|X))(?=:)/gm)].map(m=>({start:m.index,end:m.index+m[0].length,value:(m[2]||m[1]).replace('−','-'),loyalty:Boolean(m[2]),node:m[2]?costs[ci++]:mana[mi++]})).filter(item=>item.node);
}
