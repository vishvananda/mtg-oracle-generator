/** One discrete, snapping control shared by symbols and numbers. No document wheel capture. */
export function scrollPicker(host,{label,rows,columns=[''],row=0,column=0,render,change}) {
  const root=document.createElement('div');root.className='scroll-picker';root.tabIndex=0;root.setAttribute('role','group');root.setAttribute('aria-label',label);
  const grid=document.createElement('div');grid.className='scroll-grid';
  const status=document.createElement('div');status.className='scroll-status';status.setAttribute('aria-live','polite');
  root.append(grid,status);host.append(root);
  let y=Math.max(0,row),x=Math.max(0,column),timer,hoverTimer,drag=null,ignoreClick=false,wheel=0,wheelAxis='y',wheelEnd;
  const clamp=(n,max)=>Math.max(0,Math.min(max-1,n));
  function draw(){
    y=clamp(y,rows.length);x=clamp(x,columns.length);grid.replaceChildren();
    root.classList.toggle('one-axis',columns.length===1);
    for(let dy=-2;dy<=2;dy++)for(let dx=columns.length===1?0:-1;dx<=(columns.length===1?0:1);dx++){
      const b=document.createElement('button');b.type='button';b.tabIndex=-1;b.className='scroll-cell';b.style.setProperty('--distance',Math.abs(dy)+Math.abs(dx));
      const yy=y+dy,xx=x+dx;b.dataset.dy=dy;b.dataset.dx=dx;
      if(yy<0||yy>=rows.length||xx<0||xx>=columns.length){b.disabled=true;b.setAttribute('aria-hidden','true');}
      else{const r=rows[yy],c=columns[xx];render(b,r,c);b.setAttribute('aria-label',`${r.label??r} ${c.label??c}`.trim());b.setAttribute('aria-pressed',String(!dy&&!dx));b.onclick=()=>{if(!ignoreClick)step(dy,dx,true);};}
      grid.append(b);
    }
    status.textContent=`${rows[y]?.label??rows[y]??''}${columns.length>1?' · '+(columns[x]?.label??columns[x]):''}`;
  }
  function step(dy,dx=0,force=false){const a=clamp(y+dy,rows.length),b=clamp(x+dx,columns.length);if(a===y&&b===x&&!force)return;y=a;x=b;draw();change(rows[y],columns[x]);}
  function stop(){clearInterval(timer);clearTimeout(hoverTimer);timer=hoverTimer=null;}
  root.addEventListener('wheel',e=>{
    if(!rows.length)return;e.preventDefault();stop();
    const horizontal=columns.length>1&&(e.shiftKey||Math.abs(e.deltaX)>Math.abs(e.deltaY));const axis=horizontal?'x':'y';
    if(axis!==wheelAxis)wheel=0;wheelAxis=axis;wheel+=horizontal?(e.shiftKey?e.deltaY:e.deltaX):e.deltaY;
    const threshold=e.deltaMode===1?3:e.deltaMode===2?1:45;
    if(Math.abs(wheel)>=threshold){step(horizontal?0:Math.sign(wheel),horizontal?Math.sign(wheel):0);wheel=0;}
    clearTimeout(wheelEnd);wheelEnd=setTimeout(()=>{wheel=0;},160);
  },{passive:false});
  root.addEventListener('keydown',e=>{
    const move={ArrowUp:[-1,0],ArrowDown:[1,0],ArrowLeft:[0,-1],ArrowRight:[0,1]}[e.key];
    if(move){e.preventDefault();e.stopPropagation();stop();step(...move);}
  });
  root.addEventListener('pointerdown',e=>{
    if(e.button!==0)return;e.stopPropagation();stop();root.focus({preventScroll:true});drag={id:e.pointerId,x:e.clientX,y:e.clientY,moved:false,target:e.target.closest(".scroll-cell")};root.setPointerCapture(e.pointerId);
  });
  root.addEventListener('pointermove',e=>{
    e.stopPropagation();
    if(drag){
      const size=e.pointerType==='touch'?48:30,dx=e.clientX-drag.x,dy=e.clientY-drag.y;
      if(Math.max(Math.abs(dx),Math.abs(dy))<size)return;drag.moved=true;
      drag.axis??=columns.length>1&&Math.abs(dx)>Math.abs(dy)?'x':'y';
      if(drag.axis==='x'){step(0,-Math.sign(dx));drag.x=e.clientX;}
      else{step(-Math.sign(dy));drag.y=e.clientY;}return;
    }
    if(e.pointerType!=='mouse')return;
    const rect=grid.getBoundingClientRect(),dx=(e.clientX-rect.left)/rect.width-.5,dy=(e.clientY-rect.top)/rect.height-.5;
    const horizontal=columns.length>1&&Math.abs(dx)>Math.abs(dy),offset=horizontal?dx:dy;
    const dir=Math.abs(offset)>.28?Math.sign(offset):0,key=`${horizontal}:${dir}`;
    if(root.dataset.direction===key)return;stop();root.dataset.direction=key;
    if(dir)hoverTimer=setTimeout(()=>{timer=setInterval(()=>{if(!root.matches(':hover')||document.hidden){stop();return;}step(horizontal?0:dir,horizontal?dir:0);},260);},420);
  });
  function up(e){if(!drag||drag.id!==e.pointerId)return;ignoreClick=true;if(!drag.moved&&drag.target&&!drag.target.disabled)step(Number(drag.target.dataset.dy),Number(drag.target.dataset.dx),true);drag=null;if(root.hasPointerCapture(e.pointerId))root.releasePointerCapture(e.pointerId);setTimeout(()=>{ignoreClick=false;},0);}
  root.addEventListener('pointerup',up);root.addEventListener('pointercancel',()=>{drag=null;stop();});
  root.addEventListener('pointerleave',()=>{stop();delete root.dataset.direction;});root.addEventListener('blur',stop);
  const outside=e=>{if(!root.contains(e.target)){stop();delete root.dataset.direction;}};
  document.addEventListener('pointermove',outside,true);window.addEventListener('blur',stop);
  draw();return {root,reset(next,nextRow=0){rows=next;y=nextRow;draw();},dispose(){stop();clearTimeout(wheelEnd);document.removeEventListener('pointermove',outside,true);window.removeEventListener('blur',stop);root.remove();}};
}
export function glyph(button,value,url){if(url){const img=document.createElement('img');img.src=url;img.alt='';img.draggable=false;button.append(img);}else button.textContent=value;}
export const numbers=(min,max)=>Array.from({length:max-min+1},(_,i)=>String(i+min));
