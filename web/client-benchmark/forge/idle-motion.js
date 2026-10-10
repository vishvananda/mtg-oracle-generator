/** Shared gentle orbit for the preview and exported video. */
export const idlePointer=seconds=>({x:.5+Math.sin(seconds*.63)*.29,y:.5+Math.sin(seconds*.83+.7)*.23});
/** Consumer-owned motion; no face rebuilds or synthetic input events. */
export function idleMotion({element,enabled,paint}) {
  let visible=false,frame=0,started=0,last=0,moving=false,disposed=false;
  function stop(){cancelAnimationFrame(frame);frame=0;started=0;if(moving){moving=false;paint(null);}}
  function tick(time){
    frame=0;if(disposed||!visible||document.hidden||!enabled()){stop();return;}
    if(!started)started=time;
    if(time-last>=40){
      const t=(time-started)/1000,ramp=Math.min(1,t/1.2);last=time;moving=true;
      const pointer=idlePointer(t);paint({x:.5+(pointer.x-.5)*ramp,y:.5+(pointer.y-.5)*ramp});
    }
    frame=requestAnimationFrame(tick);
  }
  function wake(){if(!disposed&&visible&&!document.hidden&&enabled()){if(!frame)frame=requestAnimationFrame(tick);}else stop();}
  const observer=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;wake();});observer.observe(element);
  const poll=setInterval(wake,200);
  document.addEventListener('visibilitychange',wake);
  window.addEventListener('pagehide',stop);
  return {stop,dispose(){disposed=true;stop();clearInterval(poll);observer.disconnect();document.removeEventListener('visibilitychange',wake);window.removeEventListener('pagehide',stop);}};
}
