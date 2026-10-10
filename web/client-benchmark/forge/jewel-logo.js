// Aurum optics are baked offline. A native image leaves the GPU to the cards
// and local models; switching designs downloads only the selected animation.
import {isMobileDevice} from '/model-bench/device-support.js';
const button=document.querySelector('#jewel-toggle');
const image=button.querySelector('img');
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const valid=kind=>kind==='dual'||kind==='cube';
const query=new URLSearchParams(location.search).get('logo');
let kind=valid(query)?query:'cube';
let visible=false,revision=0;
function update(){
  const request=++revision;
  const still=reduced.matches||document.hidden||!visible||document.body.dataset.generationBusy==='true';
  const asset=new URL(`./assets/jewel-${kind}${still?'-still':''}.webp`,import.meta.url);
  asset.search=new URL(import.meta.url).search;
  const url=asset.href;
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
  update();
});
reduced.addEventListener('change',update);
document.addEventListener('visibilitychange',update);
new MutationObserver(update).observe(document.body,{attributes:true,attributeFilter:['data-generation-busy']});
new IntersectionObserver(entries=>{
  const next=!isMobileDevice&&entries[0].isIntersecting;
  visible=next;update();
}).observe(button);
update();
