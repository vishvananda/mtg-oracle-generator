/** A wheel centered on the printed item. Distance from its center controls speed. */
export function scrollPicker(host,{label,rows,columns=[''],row=0,column=0,render,change,tick=()=>{},commit=()=>{}}) {
  const root=document.createElement('div');root.className='scroll-picker direct-wheel';root.tabIndex=0;root.setAttribute('role','group');root.setAttribute('aria-label',label);
  const grid=document.createElement('div');grid.className='scroll-grid';
  const status=document.createElement('span');status.className='scroll-status';status.setAttribute('aria-live','polite');
  root.append(grid,status);host.append(root);
  const touch=matchMedia('(pointer:coarse)').matches,reduce=matchMedia('(prefers-reduced-motion:reduce)').matches;
  let y=row,x=column,raf=0,last=0,balance=0,velocity=0,armedAt=0,axis='y',press=null,ignoreClick=false,wheel=0,wheelAxis='y',wheelEnd,wheelLast=0,anchor,stepX=40,stepY=34,animation;
  const clamp=(n,max)=>Math.max(0,Math.min(max-1,n));
  function draw(dy=0,dx=0){
    y=clamp(y,rows.length);x=clamp(x,columns.length);grid.replaceChildren();root.classList.toggle('one-axis',columns.length===1);
    for(let iy=-2;iy<=2;iy++)for(let ix=columns.length===1?0:-1;ix<=(columns.length===1?0:1);ix++){
      // Four arms leave the surrounding printed card visible.
      if(iy&&ix)continue;
      const b=document.createElement('button');b.type='button';b.tabIndex=-1;b.className='scroll-cell';b.style.setProperty('--distance',Math.abs(iy)+Math.abs(ix));
      b.style.left=`${ix*stepX}px`;b.style.top=`${iy*stepY}px`;b.dataset.dy=iy;b.dataset.dx=ix;
      const yy=y+iy,xx=x+ix;
      if(yy<0||yy>=rows.length||xx<0||xx>=columns.length){b.disabled=true;b.setAttribute('aria-hidden','true');}
      else{const r=rows[yy],c=columns[xx];render(b,r,c);b.setAttribute('aria-label',`${r.label??r} ${c.label??c}`.trim());b.setAttribute('aria-pressed',String(!iy&&!ix));b.onclick=()=>{if(ignoreClick)return;stop();if(!iy&&!ix)commit();else step(iy,ix,true);};}
      grid.append(b);
    }
    status.textContent=`${rows[y]?.label??rows[y]??''}${columns.length>1?' · '+(columns[x]?.label??columns[x]):''}`;
    root.dataset.row=y;root.dataset.column=x;
    animation?.cancel();if(!reduce&&(dy||dx))animation=grid.animate([{transform:`translate(${dx*stepX}px,${dy*stepY}px)`},{transform:'translate(0,0)'}],{duration:110,easing:'cubic-bezier(.16,1,.3,1)'});
  }
  function step(dy,dx=0,force=false){const a=clamp(y+dy,rows.length),b=clamp(x+dx,columns.length);if(a===y&&b===x&&!force)return false;const deltaY=a-y,deltaX=b-x;y=a;x=b;draw(deltaY,deltaX);change(rows[y],columns[x]);if(deltaY||deltaX){tick();root.dispatchEvent(new CustomEvent('wheel-snap',{bubbles:true}));}return true;}
  function stop(){cancelAnimationFrame(raf);raf=last=balance=velocity=armedAt=0;root.dataset.speed='0';}
  function frame(now){
    raf=0;if(!velocity||document.hidden){stop();return;}
    // Moving across an option should not select it. Hold outside the neutral zone
    // deliberately, then advance at a bounded, readable pace.
    if(now>=armedAt)balance+=Math.min(80,last?now-Math.max(last,armedAt):0)*Math.abs(velocity)/1000;last=now;
    if(balance>=1){balance-=1;if(!step(axis==='y'?Math.sign(velocity):0,axis==='x'?Math.sign(velocity):0)){stop();return;}}
    raf=requestAnimationFrame(frame);
  }
  function aim(event){
    if(!anchor||event.pointerType!=='mouse'&&!press)return;
    if(event.target.closest('.scroll-set-name')){stop();return;}
    const rect=root.getBoundingClientRect(),dx=event.clientX-(rect.left+rect.width/2),dy=event.clientY-(rect.top+rect.height/2);
    const horizontal=columns.length>1&&Math.abs(dx)>Math.abs(dy)*(velocity?(axis==='x'?.75:1.35):1.15),nextAxis=horizontal?'x':'y';
    const offset=horizontal?dx:dy,dead=touch?24:16;
    if(Math.abs(offset)<=dead){stop();return;}
    const speed=Math.sign(offset)*Math.min(4,.9+Math.pow((Math.abs(offset)-dead)/(touch?38:32),1.35));
    if(axis!==nextAxis||Math.sign(speed)!==Math.sign(velocity)){balance=0;last=0;armedAt=performance.now()+300;}axis=nextAxis;velocity=speed;root.dataset.speed=String(Math.abs(speed));
    if(!raf)raf=requestAnimationFrame(frame);
  }
  root.addEventListener('wheel',e=>{
    if(!rows.length)return;e.preventDefault();stop();const horizontal=columns.length>1&&(e.shiftKey||Math.abs(e.deltaX)>Math.abs(e.deltaY));const next=horizontal?'x':'y';
    if(next!==wheelAxis)wheel=0;wheelAxis=next;wheel+=horizontal?(e.shiftKey?e.deltaY:e.deltaX):e.deltaY;
    const threshold=e.deltaMode===1?3:e.deltaMode===2?1:90;
    if(Math.abs(wheel)>=threshold){const now=performance.now();if(now-wheelLast>=180){step(horizontal?0:Math.sign(wheel),horizontal?Math.sign(wheel):0);wheelLast=now;}wheel=0;}
    clearTimeout(wheelEnd);wheelEnd=setTimeout(()=>{wheel=0;},160);
  },{passive:false});
  root.addEventListener('keydown',e=>{
    const move={ArrowUp:[-1,0],ArrowDown:[1,0],ArrowLeft:[0,-1],ArrowRight:[0,1]}[e.key];
    if(move){e.preventDefault();e.stopPropagation();stop();step(...move);}else if(e.key==='Enter'){e.preventDefault();commit();}
  });
  root.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.stopPropagation();stop();root.focus({preventScroll:true});press={id:e.pointerId,x:e.clientX,y:e.clientY,moved:false,target:e.target.closest('.scroll-cell')};root.setPointerCapture(e.pointerId);});
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
