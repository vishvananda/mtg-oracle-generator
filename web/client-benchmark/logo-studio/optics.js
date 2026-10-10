// Gem cuts and spectral internal-reflection shader adapted from Aurum v10.
// Source: user-supplied aurum-tetrahedron-source-v10.zip, deployed /aurum/.
// Offline asset rendering only; this module is never shipped to the Forge page.
import * as THREE from '../node_modules/three/build/three.module.js';
const V=(x=0,y=0,z=0)=>new THREE.Vector3(x,y,z);
const clamp=THREE.MathUtils.clamp;
const MAX_PLANES=128;
const N=16,A=.64,B=.285;
function outline(t,scale=1,z=0){const s=Math.sin(t);return V(A*Math.cos(t)*scale,B*s*Math.abs(s)*scale,z);}
function padPlanes(planes){
  const padded=[];
  for(let i=0;i<MAX_PLANES;i++)padded.push(i<planes.length?planes[i]:new THREE.Vector4(0,0,1,1e6));
  return padded;
}
function makeMarquise(){
  const rings=[{z:-.29,s:.035},{z:-.185,s:.52},{z:-.018,s:1},{z:.018,s:1},{z:.098,s:.78},{z:.172,s:.43}];
  const ps=[],norms=[],planeList=[],facets=[],sparkPoints=[];
  function face(a,b,c){
    let n=V().crossVectors(V().subVectors(b,a),V().subVectors(c,a)).normalize();
    let centroid=a.clone().add(b).add(c).multiplyScalar(1/3);
    if(n.dot(centroid)<0){[b,c]=[c,b];n.negate();}
    if(n.lengthSq()<.5)return;
    [a,b,c].forEach(p=>{ps.push(p.x,p.y,p.z);norms.push(n.x,n.y,n.z);});
    const d=-n.dot(a);
    if(!planeList.some(p=>Math.abs(p.x-n.x)+Math.abs(p.y-n.y)+Math.abs(p.z-n.z)+Math.abs(p.w-d)<1e-5))planeList.push(new THREE.Vector4(n.x,n.y,n.z,d));
    facets.push({p:centroid,n:n.clone()});
  }
  const ringPts=rings.map(r=>Array.from({length:N},(_,i)=>outline(i*2*Math.PI/N,r.s,r.z)));
  for(let r=0;r<rings.length-1;r++)for(let i=0;i<N;i++){
    const j=(i+1)%N;face(ringPts[r][i],ringPts[r][j],ringPts[r+1][i]);face(ringPts[r][j],ringPts[r+1][j],ringPts[r+1][i]);
  }
  for(let i=0;i<N;i++){
    const j=(i+1)%N;face(V(0,0,rings[0].z),ringPts[0][j],ringPts[0][i]);
    face(V(0,0,rings.at(-1).z),ringPts.at(-1)[i],ringPts.at(-1)[j]);
  }
  for(let i=0;i<N;i+=2){sparkPoints.push({p:ringPts[4][i].clone(),n:V(ringPts[4][i].x*.6,ringPts[4][i].y*2,1).normalize()});}
  const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.Float32BufferAttribute(ps,3));geo.setAttribute('normal',new THREE.Float32BufferAttribute(norms,3));
  geo.computeBoundingSphere();geo.computeBoundingBox();
  return {geometry:geo,planes:padPlanes(planeList),planeCount:planeList.length,facets,sparkPoints,label:'Marquise'};
}
const SPINDLE_SECTIONS=[{x:-.68,r:0},{x:-.50,r:.11},{x:-.28,r:.205},{x:-.06,r:.285},{x:.06,r:.285},{x:.28,r:.205},{x:.50,r:.11},{x:.68,r:0}];
function spindleRadiusAt(x){
  x=Math.abs(x);
  for(let i=4;i<SPINDLE_SECTIONS.length-1;i++){
    const a=SPINDLE_SECTIONS[i],b=SPINDLE_SECTIONS[i+1];
    if(x<=b.x){const t=clamp((x-a.x)/(b.x-a.x),0,1);return a.r+(b.r-a.r)*t;}
  }
  return 0;
}
function makeSpindle(){
  const M=10,sections=SPINDLE_SECTIONS;
  const ps=[],norms=[],planeList=[],sparkPoints=[];
  function face(a,b,c){
    let n=V().crossVectors(V().subVectors(b,a),V().subVectors(c,a)).normalize();
    let centroid=a.clone().add(b).add(c).multiplyScalar(1/3);
    if(n.dot(centroid)<0){[b,c]=[c,b];n.negate();}
    if(n.lengthSq()<.5)return;
    [a,b,c].forEach(p=>{ps.push(p.x,p.y,p.z);norms.push(n.x,n.y,n.z);});
    const d=-n.dot(a);
    if(!planeList.some(p=>Math.abs(p.x-n.x)+Math.abs(p.y-n.y)+Math.abs(p.z-n.z)+Math.abs(p.w-d)<1e-5))planeList.push(new THREE.Vector4(n.x,n.y,n.z,d));
  }
  const rings=sections.map(section=>section.r===0?null:Array.from({length:M},(_,i)=>{
    const t=i*2*Math.PI/M;return V(section.x,Math.cos(t)*section.r,Math.sin(t)*section.r);
  }));
  const leftTip=V(sections[0].x,0,0),rightTip=V(sections.at(-1).x,0,0);
  for(let i=0;i<M;i++){
    const j=(i+1)%M;face(leftTip,rings[1][j],rings[1][i]);
    face(rings.at(-2)[i],rings.at(-2)[j],rightTip);
  }
  for(let s=1;s<sections.length-2;s++){
    const ringA=rings[s],ringB=rings[s+1];
    for(let i=0;i<M;i++){
      const j=(i+1)%M;
      face(ringA[i],ringA[j],ringB[i]);
      face(ringA[j],ringB[j],ringB[i]);
    }
  }
  const sparkleRing=rings[4];
  for(let i=0;i<M;i+=2){const p=sparkleRing[i].clone();sparkPoints.push({p,n:V(.18,p.y*3,p.z*3).normalize()});}
  const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.Float32BufferAttribute(ps,3));geo.setAttribute('normal',new THREE.Float32BufferAttribute(norms,3));
  geo.computeBoundingSphere();geo.computeBoundingBox();
  return {geometry:geo,planes:padPlanes(planeList),planeCount:planeList.length,sparkPoints,label:'Spindle'};
}
const cuts={marquise:makeMarquise(),spindle:makeSpindle()};

const gemVertex=`
varying vec3 vLocalPosition;
varying vec3 vLocalNormal;
varying vec3 vWorldPosition;
void main(){
  vLocalPosition=position;
  vLocalNormal=normal;
  vWorldPosition=(modelMatrix*vec4(position,1.0)).xyz;
  gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);
}`;
const gemFragment=`
precision highp float;
#define MAX_PLANES ${MAX_PLANES}
uniform vec4 uPlanes[MAX_PLANES];
uniform int uPlaneCount;
uniform mat4 uInvModel;
uniform mat4 uWorld;
uniform float uIor;
uniform float uDispersion;
uniform float uBrilliance;
uniform float uTransmission;
uniform float uTime;
uniform float uLightAngle;
uniform float uLightMode;
uniform float uBlack;
uniform vec3 uTint;
uniform vec3 uAbsorption;
varying vec3 vLocalPosition;
varying vec3 vLocalNormal;
varying vec3 vWorldPosition;
vec3 rotateY(vec3 d,float a){float c=cos(a),s=sin(a);return vec3(c*d.x+s*d.z,d.y,-s*d.x+c*d.z);}
float panel(vec3 ray,vec3 direction,vec2 size){
  vec3 forward=normalize(direction);
  vec3 helper=abs(forward.y)>.95?vec3(0.,0.,1.):vec3(0.,1.,0.);
  vec3 right=normalize(cross(helper,forward));vec3 up=cross(forward,right);
  float facing=dot(ray,forward);if(facing<=.01)return 0.;
  vec2 uv=abs(vec2(dot(ray,right),dot(ray,up))/facing)/size;
  return exp(-pow(min(uv.x,10.),12.)-pow(min(uv.y,10.),12.));
}
vec3 studio(vec3 d){
  d=rotateY(normalize(d),uLightAngle);
  vec3 c=vec3(.043,.050,.065)+max(0.,d.y)*vec3(.11,.12,.145);
  c+=panel(d,vec3(-.65,.65,.70),vec2(.31,.72))*vec3(1.,.90,.73)*7.4;
  c+=panel(d,vec3(.72,.30,.50),vec2(.16,.88))*vec3(.80,.90,1.)*9.;
  c+=panel(d,vec3(.32,.72,-.80),vec2(.48,.34))*vec3(1.,.97,.92)*10.;
  c+=panel(d,vec3(-.8,.05,-.48),vec2(.13,.72))*vec3(.81,.88,1.)*4.7;
  c+=panel(d,vec3(.1,.98,.06),vec2(.48,.46))*vec3(1.,.95,.86)*3.;
  c+=pow(max(dot(d,normalize(vec3(sin(uTime*.27)*.8,.65,cos(uTime*.27)))),0.),520.)*16.*uBrilliance;
  c+=pow(max(dot(d,normalize(vec3(-.6,.18,.77))),0.),900.)*11.*uBrilliance;
  if(uLightMode>.5&&uLightMode<1.5)c=mix(c,vec3(dot(c,vec3(.3,.59,.11))),.65)*1.15+vec3(.12);
  if(uLightMode>1.5)c*=vec3(.82+.22*sin(d.x*6.),.85+.22*sin(d.y*7.+2.),1.03+.2*sin(d.z*6.+4.));
  return c;
}
vec3 worldDirection(vec3 d){return normalize(mat3(uWorld)*d);}
float fresnel(float cosine,float eta){float f=(eta-1.)/(eta+1.);return f*f+(1.-f*f)*pow(1.-clamp(cosine,0.,1.),5.);}
float channelValue(vec3 c,int channel){if(channel==0)return c.r;if(channel==1)return c.g;return c.b;}
float traceChannel(vec3 incoming,vec3 normal,float ior,int channel){
  vec3 dir=refract(incoming,normal,1./ior);
  vec3 p=vLocalPosition+dir*.00025;
  float energy=1.,radiance=0.;
  float absorption=channelValue(uAbsorption,channel);
  for(int bounce=0;bounce<5;bounce++){
    float distance=1000.;vec3 exitNormal=normal;
    for(int j=0;j<MAX_PLANES;j++){
      if(j>=uPlaneCount)break;
      vec3 n=uPlanes[j].xyz;float denominator=dot(n,dir);
      if(denominator>.00001){
        float t=-(dot(n,p)+uPlanes[j].w)/denominator;
        if(t>.00002&&t<distance){distance=t;exitNormal=n;}
      }
    }
    if(distance>900.)break;
    p+=dir*distance;
    energy*=exp(-absorption*distance*(1.40-.65*uTransmission));
    vec3 transmitted=refract(dir,-exitNormal,ior);
    float cosOut=clamp(dot(dir,exitNormal),0.,1.);
    float f=fresnel(cosOut,ior);
    if(dot(transmitted,transmitted)>.001){
      float light=channelValue(studio(worldDirection(transmitted)),channel);
      radiance+=energy*(1.-f)*light;
      energy*=f;
    }
    dir=reflect(dir,exitNormal);p+=dir*.00025;
    if(energy<.008)break;
  }
  radiance+=energy*channelValue(studio(worldDirection(dir)),channel)*.18;
  return radiance;
}
void main(){
  vec3 eye=(uInvModel*vec4(cameraPosition,1.)).xyz;
  vec3 incoming=normalize(vLocalPosition-eye);
  vec3 n=normalize(vLocalNormal);if(dot(n,incoming)>0.)n=-n;
  float spectral=.018*uDispersion;
  vec3 transmitted=vec3(traceChannel(incoming,n,uIor-spectral,0),traceChannel(incoming,n,uIor,1),traceChannel(incoming,n,uIor+spectral,2));
  float f=fresnel(-dot(n,incoming),uIor);
  vec3 reflection=studio(worldDirection(reflect(incoming,n)));
  vec3 c=mix(uTint*.12,transmitted,uTransmission)*(1.-f)+reflection*f;
  c+=uTint*(.025+.025*uBrilliance)*(1.-uBlack*.75);
  c*=1.+.22*uBrilliance;
  gl_FragColor=vec4(c,1.);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

export {cuts,gemVertex,gemFragment};
