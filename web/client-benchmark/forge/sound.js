// Original interface cues plus the existing Adventure opening theme.
// Audio assets are requested only after interaction; muting controls both.
export class Sound {
  constructor(){
    try{this.enabled=localStorage.getItem('forge-sound')!=='off';}catch{this.enabled=true;}
    const unlock=event=>{if(event.target instanceof Element&&event.target.closest('#sound-toggle'))return;if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;void this.startMusic();};
    document.addEventListener('pointerdown',unlock,{passive:true});document.addEventListener('keydown',unlock);
    document.addEventListener('visibilitychange',()=>{if(document.hidden){this.music?.pause();void this.context?.suspend();}else if(this.started&&this.enabled)void this.startMusic();});
    window.addEventListener('pagehide',()=>{this.music?.pause();void this.context?.suspend();});
    window.addEventListener('pageshow',()=>{if(this.started&&this.enabled)void this.startMusic();});
  }
  async startMusic(){
    if(!this.enabled||document.hidden)return;
    try{
      this.context??=new (window.AudioContext||window.webkitAudioContext)();
      if(!this.music){
        this.music=new Audio(new URL('./assets/opening.m4a',import.meta.url));this.music.preload='none';this.music.loop=true;this.music.hidden=true;this.music.dataset.forgeMusic='opening';document.body.append(this.music);
        const source=this.context.createMediaElementSource(this.music),gain=this.context.createGain();gain.gain.value=.22;source.connect(gain);gain.connect(this.context.destination);
      }
      await this.context.resume();await this.music.play();this.started=true;
    }catch{/* A later gesture can retry browser audio unlock. */}
  }
  set(value){this.enabled=value;try{localStorage.setItem('forge-sound',value?'on':'off');}catch{}if(!value){this.music?.pause();void this.context?.suspend();}else void this.startMusic();}
  async play(kind){
    if(!this.enabled||document.hidden)return;
    try {
      if(kind==='tick'){
        // A dry detent click: one short cached noise buffer, no music restart.
        this.context??=new (window.AudioContext||window.webkitAudioContext)();const ctx=this.context;await ctx.resume();
        if(!this.clickBuffer){this.clickBuffer=ctx.createBuffer(1,Math.ceil(ctx.sampleRate*.028),ctx.sampleRate);const samples=this.clickBuffer.getChannelData(0);for(let i=0;i<samples.length;i++)samples[i]=(Math.random()*2-1)*Math.exp(-i/(ctx.sampleRate*.004));}
        const source=ctx.createBufferSource(),filter=ctx.createBiquadFilter(),gain=ctx.createGain();source.buffer=this.clickBuffer;filter.type='bandpass';filter.frequency.value=1700;filter.Q.value=.8;gain.gain.value=.2;source.connect(filter);filter.connect(gain);gain.connect(ctx.destination);source.onended=()=>{source.disconnect();filter.disconnect();gain.disconnect();};source.start();return;
      }
      void this.startMusic();
      this.context??=new (window.AudioContext||window.webkitAudioContext)();await this.context.resume();
      const ctx=this.context,now=ctx.currentTime;
      const cues={load:[[220,0,.25],[330,.06,.4],[440,.12,.5]],edit:[[740,0,.12],[1110,.035,.2]],start:[[196,0,.25],[293.66,.09,.35],[392,.18,.55]],complete:[[523.25,0,.5],[659.25,.12,.65],[783.99,.24,.9]],click:[[660,0,.07]]};
      for(const [freq,delay,duration] of cues[kind]||cues.click){const o=ctx.createOscillator(),g=ctx.createGain();o.type='sine';o.frequency.setValueAtTime(freq,now+delay);g.gain.setValueAtTime(0,now+delay);g.gain.linearRampToValueAtTime(.025,now+delay+.015);g.gain.exponentialRampToValueAtTime(.0001,now+delay+duration);o.connect(g);g.connect(ctx.destination);o.start(now+delay);o.stop(now+delay+duration+.02);}
    }catch{/* Sound is optional. */}
  }
}
