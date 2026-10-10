// Aurum optics are baked offline. A native image leaves the GPU to the cards
// and local models; switching designs downloads only the selected animation.
const button=document.querySelector('#jewel-toggle');
const image=button.querySelector('img');
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const valid=kind=>kind==='dual'||kind==='cube';
let stored;
try{stored=localStorage.getItem('forge-jewel');}catch{}
const query=new URLSearchParams(location.search).get('logo');
let kind=valid(query)?query:valid(stored)?stored:'dual';
let visible=true,revision=0;
function update(){
  const request=++revision;
  const still=reduced.matches||document.hidden||!visible;
  const url=new URL(`./assets/jewel-${kind}${still?'-still':''}.webp`,import.meta.url).href;
  const current=kind==='dual'?'Dual tetrahedron':'Cube';
  const next=kind==='dual'?'cube':'dual tetrahedron';
  button.setAttribute('aria-label',`${current} jewel. Switch to ${next}`);
  button.title=`Switch to ${next} jewel`;
  button.dataset.design=kind;
  button.dataset.motion=still?'still':'spinning';
  if(image.src===url)return;
  const ready=new Image();ready.src=url;
  ready.decode().then(()=>{if(revision===request)image.src=url;}).catch(()=>{
    // Leave the last complete image on screen if a download fails.
  });
}
button.addEventListener('click',()=>{
  kind=kind==='dual'?'cube':'dual';
  try{localStorage.setItem('forge-jewel',kind);}catch{}
  update();
});
reduced.addEventListener('change',update);
document.addEventListener('visibilitychange',update);
new IntersectionObserver(entries=>{
  const next=entries[0].isIntersecting;
  if(next!==visible){visible=next;update();}
}).observe(button);
update();
