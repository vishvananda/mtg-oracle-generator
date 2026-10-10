/** A wheel centered on the printed item. Distance from its center controls speed. */
export function scrollPicker(host,{label,rows,columns=[''],row=0,column=0,render,change,tick=()=>{},commit=()=>{}}) {
  const root=document.createElement('div');root.className='scroll-picker direct-wheel';root.tabIndex=0;root.setAttribute('role','group');root.setAttribute('aria-label',label);
  const grid=document.createElement('div');grid.className='scroll-grid';
  const status=document.createElement('span');status.className='scroll-status';status.setAttribute('aria-live','polite');
  root.append(grid,status);host.append(root);
  const touch=matchMedia('(pointer:coarse)').matches,reduce=matchMedia('(prefers-reduced-motion:reduce)').matches;
  let y=row,x=column,raf=0,last=0,balance=0,velocity=0,targetVelocity=0,travel=0,armedAt=0,axis='y',press=null,ignoreClick=false,wheel=0,wheelAxis='y',wheelEnd,wheelLast=0,wheelEventAt=-Infinity,anchor,stepX=40,stepY=34,animation;
  const clamp=(n,max)=>Math.max(0,Math.min(max-1,n));
  function draw(dy=0,dx=0,continuous=false){
    y=clamp(y,rows.length);x=clamp(x,columns.length);grid.replaceChildren();root.classList.toggle('one-axis',columns.length===1);
    for(let iy=-2;iy<=2;iy++)for(let ix=columns.length===1?0:-1;ix<=(columns.length===1?0:1);ix++){
      // Four arms leave the surrounding printed card visible.
      if(iy&&ix)continue;
      const b=document.createElement('button');b.type='button';b.tabIndex=-1;b.className='scroll-cell';b.style.setProperty('--distance',Math.abs(iy)+Math.abs(ix));
      b.style.left=`${ix*stepX}px`;b.style.top=`${iy*stepY}px`;b.dataset.dy=iy;b.dataset.dx=ix;
      const yy=y+iy,xx=x+ix;
      if(yy<0||yy>=rows.length||xx<0||xx>=columns.length){b.disabled=true;b.setAttribute('aria-hidden','true');}
      else{const r=rows[yy],c=columns[xx];render(b,r,c);b.setAttribute('aria-label',`${r.label??r} ${c.label??c}`.trim());b.setAttribute('aria-pressed',String(!iy&&!ix));b.onclick=()=>{if(ignoreClick)return;stop(false);if(!iy&&!ix)commit();else step(iy,ix,true);};}
      grid.append(b);
    }
    status.textContent=`${rows[y]?.label??rows[y]??''}${columns.length>1?' · '+(columns[x]?.label??columns[x]):''}`;
    root.dataset.row=y;root.dataset.column=x;
    animation?.cancel();grid.style.transform='';if(!continuous&&!reduce&&(dy||dx))animation=grid.animate([{transform:`translate(${dx*stepX}px,${dy*stepY}px)`},{transform:'translate(0,0)'}],{duration:110,easing:'cubic-bezier(.16,1,.3,1)'});
  }
  function step(dy,dx=0,force=false,continuous=false){const a=clamp(y+dy,rows.length),b=clamp(x+dx,columns.length);if(a===y&&b===x&&!force)return false;const deltaY=a-y,deltaX=b-x;y=a;x=b;draw(deltaY,deltaX,continuous);change(rows[y],columns[x]);if(deltaY||deltaX){tick();root.dispatchEvent(new CustomEvent('wheel-snap',{bubbles:true}));}return true;}
  function stop(settle=true){
    cancelAnimationFrame(raf);clearTimeout(wheelEnd);
    if(travel){
      const direction=Math.sign(travel);
      if(settle&&Math.abs(travel)>=.5&&step(0,direction,false,true))travel-=direction;
      const from=-travel*stepX;
      for(const cell of grid.children)if(Number(cell.dataset.dy)===0){
        cell.getAnimations().forEach(a=>a.cancel());cell.style.translate='';
        if(!reduce)cell.animate([{translate:`${from}px 0`},{translate:'0px 0px'}],{duration:170,easing:'cubic-bezier(.2,.8,.2,1)'});
      }
    }
    raf=last=balance=velocity=targetVelocity=travel=armedAt=0;root.dataset.speed='0';root.dataset.targetSpeed='0';root.dataset.progress='0';
  }
  function slide(distance){
    animation?.cancel();travel+=distance;
    while(Math.abs(travel)>=1){const direction=Math.sign(travel);if(!step(0,direction,false,true)){travel=0;break;}travel-=direction;}
    if(x===0&&travel<0||x===columns.length-1&&travel>0)travel=0;
    // Only the rarity row moves sideways. Keep neighboring set names stationary.
    for(const cell of grid.children)if(Number(cell.dataset.dy)===0){cell.getAnimations().forEach(a=>a.cancel());cell.style.translate=reduce?'':`${-travel*stepX}px 0`;}
    root.dataset.progress=String(travel);
  }
  function frame(now){
    raf=0;if(!(axis==='x'?targetVelocity:velocity)||document.hidden){stop();return;}
    if(axis==='x'){
      const elapsed=Math.min(64,last?now-last:0);last=now;
      velocity+=(targetVelocity-velocity)*(1-Math.exp(-elapsed/180));
      slide(velocity*elapsed/1000);root.dataset.speed=String(Math.abs(velocity));
      raf=requestAnimationFrame(frame);return;
    }
    // Moving across an option should not select it. Hold outside the neutral zone
    // deliberately, then advance at a bounded, readable pace.
    if(now>=armedAt)balance+=Math.min(80,last?now-Math.max(last,armedAt):0)*Math.abs(velocity)/1000;last=now;
    if(balance>=1){balance-=1;if(!step(axis==='y'?Math.sign(velocity):0,axis==='x'?Math.sign(velocity):0)){stop();return;}}
    raf=requestAnimationFrame(frame);
  }
  function aim(event){
    if(!anchor||event.pointerType!=='mouse'&&!press)return;
    if(performance.now()-wheelEventAt<220)return;
    const rect=root.getBoundingClientRect(),dx=event.clientX-(rect.left+rect.width/2),dy=event.clientY-(rect.top+rect.height/2);
    const horizontal=columns.length>1&&Math.abs(dx)>Math.abs(dy)*(targetVelocity||velocity?(axis==='x'?.65:1.35):1.15),nextAxis=horizontal?'x':'y';
    if(event.target.closest('.scroll-set-name')&&!(horizontal&&axis==='x'&&targetVelocity&&Math.abs(dx)<rect.width/2)){stop();return;}
    const offset=horizontal?dx:dy,dead=horizontal?(touch?12:8):(touch?24:16);
    if(Math.abs(offset)<=dead){stop();return;}
    if(horizontal){
      if(axis!=='x'){stop();axis='x';}
      const range=Math.max(20,Math.min(36,rect.width/2-dead-4)),fraction=Math.min(1,(Math.abs(offset)-dead)/range);
      targetVelocity=Math.sign(offset)*2.4*fraction*fraction*(3-2*fraction);
      root.dataset.targetSpeed=String(Math.abs(targetVelocity));
      if(!raf)raf=requestAnimationFrame(frame);return;
    }
    if(axis!=='y'){stop();axis='y';}
    const speed=Math.sign(offset)*Math.min(4,.9+Math.pow((Math.abs(offset)-dead)/(touch?38:32),1.35));
    if(axis!==nextAxis||Math.sign(speed)!==Math.sign(velocity)){balance=0;last=0;armedAt=performance.now()+300;}axis=nextAxis;velocity=speed;root.dataset.speed=String(Math.abs(speed));
    if(!raf)raf=requestAnimationFrame(frame);
  }
  root.addEventListener('wheel',e=>{
    if(!rows.length)return;e.preventDefault();const now=performance.now(),continuing=now-wheelEventAt<220;
    const horizontal=columns.length>1&&(e.shiftKey||(continuing?wheelAxis==='x':Math.abs(e.deltaX)>Math.abs(e.deltaY))),next=horizontal?'x':'y';
    if(horizontal){
      if(!continuing||wheelAxis!=='x'){stop(false);wheel=0;}else{cancelAnimationFrame(raf);raf=0;}
      wheelAxis=axis='x';wheelEventAt=now;velocity=targetVelocity=0;
      const pixels=(e.shiftKey?e.deltaY:e.deltaX)*(e.deltaMode===1?24:e.deltaMode===2?90:1);
      slide(Math.max(-1,Math.min(1,pixels/90)));
      clearTimeout(wheelEnd);wheelEnd=setTimeout(()=>stop(),180);return;
    }
    stop();wheelEventAt=now;
    if(next!==wheelAxis)wheel=0;wheelAxis=next;wheel+=horizontal?(e.shiftKey?e.deltaY:e.deltaX):e.deltaY;
    const threshold=e.deltaMode===1?3:e.deltaMode===2?1:90;
    if(Math.abs(wheel)>=threshold){const now=performance.now();if(now-wheelLast>=180){step(horizontal?0:Math.sign(wheel),horizontal?Math.sign(wheel):0);wheelLast=now;}wheel=0;}
    clearTimeout(wheelEnd);wheelEnd=setTimeout(()=>{wheel=0;},160);
  },{passive:false});
  root.addEventListener('keydown',e=>{
    const move={ArrowUp:[-1,0],ArrowDown:[1,0],ArrowLeft:[0,-1],ArrowRight:[0,1]}[e.key];
    if(move){e.preventDefault();e.stopPropagation();stop(false);step(...move);}else if(e.key==='Enter'){e.preventDefault();stop();commit();}
  });
  root.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.stopPropagation();stop(false);root.focus({preventScroll:true});press={id:e.pointerId,x:e.clientX,y:e.clientY,moved:false,target:e.target.closest('.scroll-cell')};root.setPointerCapture(e.pointerId);});
  root.addEventListener('pointermove',e=>{e.stopPropagation();if(press&&Math.hypot(e.clientX-press.x,e.clientY-press.y)>5)press.moved=true;aim(e);});
  root.addEventListener('pointerup',e=>{
    if(!press||e.pointerId!==press.id)return;const p=press;press=null;stop();ignoreClick=true;
    if(root.hasPointerCapture(e.pointerId))root.releasePointerCapture(e.pointerId);
    if(!p.moved&&p.target&&!p.target.disabled){const dy=Number(p.target.dataset.dy),dx=Number(p.target.dataset.dx);if(dy||dx)step(dy,dx,true);else commit();}
    setTimeout(()=>{ignoreClick=false;},0);
  });
  root.addEventListener('pointercancel',()=>{press=null;stop();});root.addEventListener('lostpointercapture',()=>{press=null;stop();});
  root.addEventListener('pointerleave',()=>{if(!press)stop();});root.addEventListener('blur',stop);
  const outside=e=>{if(!press&&!root.contains(e.target))stop();};
  document.addEventListener('pointermove',outside,true);document.addEventListener('visibilitychange',stop);window.addEventListener('blur',stop);
  function place(rect,font){
    anchor=rect;stepY=touch?54:Math.max(30,rect.height*1.6);stepX=touch?55:Math.max(38,rect.width*1.7);
    const centerX=host.getBoundingClientRect().left+rect.x+rect.width/2;stepX=Math.max(rect.width+5,Math.min(stepX,innerWidth-centerX-rect.width/2-8,centerX-rect.width/2-8));
    const w=columns.length>1?2*(stepX+rect.width/2+5):Math.max(touch?48:32,rect.width+12),h=stepY*5;
    Object.assign(root.style,{left:`${rect.x+rect.width/2}px`,top:`${rect.y+rect.height/2}px`,width:`${w}px`,height:`${h}px`});
    root.style.setProperty('--glyph-w',`${rect.width}px`);root.style.setProperty('--glyph-h',`${rect.height}px`);root.style.setProperty('--step-y',`${stepY}px`);
    if(font)Object.assign(root.style,{fontFamily:font.fontFamily,fontWeight:font.fontWeight,fontStyle:font.fontStyle,fontSize:font.fontSize,color:font.color,letterSpacing:font.letterSpacing});draw();
  }
  draw();return {root,place,reset(next,nextRow=0){stop();rows=next;y=nextRow;draw();},dispose(){stop();animation?.cancel();clearTimeout(wheelEnd);document.removeEventListener('pointermove',outside,true);document.removeEventListener('visibilitychange',stop);window.removeEventListener('blur',stop);root.remove();}};
}
export function glyph(button,value,url){if(url){const img=document.createElement('img');img.src=url;img.alt='';img.draggable=false;button.append(img);}else button.textContent=value;}
export const numbers=(min,max)=>Array.from({length:max-min+1},(_,i)=>String(i+min));
