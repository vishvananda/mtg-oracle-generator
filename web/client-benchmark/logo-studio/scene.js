// Aurum's studio lighting, gold, nacre and spectral gemstone optics, adapted
// into deterministic transparent logo frames. Never imported by the live page.
import * as THREE from '../node_modules/three/build/three.module.js';
import {cuts,gemVertex,gemFragment} from './optics.js';
const V=(x=0,y=0,z=0)=>new THREE.Vector3(x,y,z);
const renderer=new THREE.WebGLRenderer({alpha:true,antialias:true,preserveDrawingBuffer:true});
renderer.setSize(256,256);renderer.setPixelRatio(1);
renderer.outputColorSpace=THREE.SRGBColorSpace;
renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.10;
renderer.setClearColor(0,0);document.body.append(renderer.domElement);
const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-2.35,2.35,2.35,-2.35,.1,100);
camera.position.set(0,.28,10);camera.lookAt(0,0,0);
const panels=[
  {dir:V(-.65,.65,.70).normalize(),size:[.31,.72],color:[1,.90,.73],power:7.4},
  {dir:V(.72,.3,.50).normalize(),size:[.16,.88],color:[.80,.90,1],power:9},
  {dir:V(.32,.72,-.80).normalize(),size:[.48,.34],color:[1,.97,.92],power:10},
  {dir:V(-.80,.05,-.48).normalize(),size:[.13,.72],color:[.81,.88,1],power:4.7},
  {dir:V(.1,.98,.06).normalize(),size:[.48,.46],color:[1,.95,.86],power:3}
];
function environment(){
  const w=384,h=192,data=new Float32Array(w*h*4),d=V();
  const basis=panels.map(p=>{const right=V().crossVectors(Math.abs(p.dir.y)>.95?V(0,0,1):V(0,1,0),p.dir).normalize();return {...p,right,up:V().crossVectors(p.dir,right).normalize()};});
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){
    const phi=((x+.5)/w-.5)*Math.PI*2,theta=(1-(y+.5)/h)*Math.PI;
    d.set(Math.sin(theta)*Math.cos(phi),Math.cos(theta),Math.sin(theta)*Math.sin(phi));
    const ambient=.045+.11*Math.max(0,d.y),c=[ambient*.94,ambient,ambient*1.13];
    for(const p of basis){const facing=d.dot(p.dir);if(facing<.01)continue;
      const sx=Math.abs(d.dot(p.right)/facing)/p.size[0],sy=Math.abs(d.dot(p.up)/facing)/p.size[1];
      const mask=Math.exp(-Math.pow(sx,16)-Math.pow(sy,16))*p.power;
      for(let i=0;i<3;i++)c[i]+=mask*p.color[i];
    }
    data.set([...c,1],(y*w+x)*4);
  }
  const texture=new THREE.DataTexture(data,w,h,THREE.RGBAFormat,THREE.FloatType);
  texture.mapping=THREE.EquirectangularReflectionMapping;texture.colorSpace=THREE.LinearSRGBColorSpace;texture.needsUpdate=true;
  const pmrem=new THREE.PMREMGenerator(renderer),target=pmrem.fromEquirectangular(texture);
  texture.dispose();pmrem.dispose();return target.texture;
}
scene.environment=environment();
scene.add(new THREE.HemisphereLight(0xe4efff,0x716047,1));
for(const [color,intensity,p]of [[0xffedd6,3.2,V(-8,17,10)],[0xc9deff,2,V(6,2,2)],[0xffffff,4,V(1,5,-5)]]){const light=new THREE.DirectionalLight(color,intensity);light.position.copy(p);scene.add(light);}
const gold=new THREE.MeshPhysicalMaterial({color:0xe8bc66,metalness:1,roughness:.20,clearcoat:.5,clearcoatRoughness:.14,envMapIntensity:1.25});
const polished=new THREE.MeshPhysicalMaterial({color:0xf0c876,metalness:1,roughness:.14,clearcoat:.6,envMapIntensity:1.4});
const nacreCanvas=document.createElement('canvas');nacreCanvas.width=nacreCanvas.height=192;
const nc=nacreCanvas.getContext('2d'),nacre=nc.createImageData(192,192);
for(let y=0;y<192;y++)for(let x=0;x<192;x++){
  const u=x/192,v=y/192,n=.5+.15*Math.sin(50*v+2*Math.sin(12*u))+.09*Math.sin(130*v+7*Math.cos(17*u))+.025*Math.sin(398*u+212*v),i=(y*192+x)*4;
  nacre.data[i]=nacre.data[i+1]=nacre.data[i+2]=Math.round(THREE.MathUtils.clamp(n,0,1)*255);nacre.data[i+3]=255;
}
nc.putImageData(nacre,0,0);
const nacreMap=new THREE.CanvasTexture(nacreCanvas);nacreMap.wrapS=nacreMap.wrapT=THREE.RepeatWrapping;
const pearlGeometry=new THREE.SphereGeometry(.1425,48,32),rodGeometry=new THREE.CylinderGeometry(1,1,1,12);
const cupGeometry=new THREE.LatheGeometry([new THREE.Vector2(.029,-.25),new THREE.Vector2(.093,-.233),new THREE.Vector2(.163,-.198),new THREE.Vector2(.233,-.14),new THREE.Vector2(.285,-.076),new THREE.Vector2(.305,-.017)],32);
const ringGeometry=new THREE.TorusGeometry(.267,.0175,8,32);
function rod(parent,a,b,r=.035){const d=b.clone().sub(a),mesh=new THREE.Mesh(rodGeometry,gold);mesh.position.copy(a).add(b).multiplyScalar(.5);mesh.quaternion.setFromUnitVectors(V(0,1,0),d.clone().normalize());mesh.scale.set(r,d.length(),r);parent.add(mesh);}
function pearl(parent,vertex){
  const mat=new THREE.MeshPhysicalMaterial({color:vertex.color,metalness:.04,roughness:.235,clearcoat:1,clearcoatRoughness:.12,iridescence:.34,iridescenceIOR:1.33,iridescenceThicknessRange:[260,410],iridescenceThicknessMap:nacreMap,bumpMap:nacreMap,bumpScale:.0045,envMapIntensity:1.15,specularIntensity:.85});
  const mesh=new THREE.Mesh(pearlGeometry,mat);mesh.position.copy(vertex.position);parent.add(mesh);
  const outward=vertex.position.clone().normalize(),cup=new THREE.Group();cup.position.copy(vertex.position).addScaledVector(outward,-.022);cup.quaternion.setFromUnitVectors(V(0,1,0),outward);
  cup.add(new THREE.Mesh(cupGeometry,polished));const ring=new THREE.Mesh(ringGeometry,polished);ring.rotation.x=Math.PI/2;ring.position.y=-.032;cup.add(ring);cup.scale.setScalar(.5);parent.add(cup);
}
// Arithmetic midpoint in linear-light RGB, before the shader's lighting,
// refraction and tone mapping. White makes a pastel; black deepens the hue.
function midpoint(a,b){return new THREE.Color(a).lerp(new THREE.Color(b),.5);}
function gemstone(parent,a,b,tint){
  const d=b.clone().sub(a),length=d.length(),center=a.clone().add(b).multiplyScalar(.5),direction=d.clone().normalize();
  const scale=.75*Math.min(.90,(length-.72)/1.36),halfGem=.68*scale;
  rod(parent,a.clone().addScaledVector(direction,.095),center.clone().addScaledVector(direction,-halfGem+.025));
  rod(parent,center.clone().addScaledVector(direction,halfGem-.025),b.clone().addScaledVector(direction,-.095));
  const absorption=V(...tint.toArray().map(c=>-Math.log(Math.max(.008,c))*.85));
  const uniforms={uPlanes:{value:cuts.spindle.planes},uPlaneCount:{value:cuts.spindle.planeCount},uInvModel:{value:new THREE.Matrix4()},uWorld:{value:new THREE.Matrix4()},uIor:{value:1.77},uDispersion:{value:1.15},uBrilliance:{value:1.15},uTransmission:{value:.90},uTime:{value:0},uLightAngle:{value:0},uLightMode:{value:0},uBlack:{value:0},uTint:{value:tint},uAbsorption:{value:absorption}};
  // Preserve Aurum's optics while allowing the page behind the stone to show
  // through. Reflections at grazing angles remain stronger; gold/pearls stay opaque.
  const translucentFragment=gemFragment.replace('gl_FragColor=vec4(c,1.);','gl_FragColor=vec4(c,mix(.76,.96,f));');
  const material=new THREE.ShaderMaterial({uniforms,vertexShader:gemVertex,fragmentShader:translucentFragment,side:THREE.FrontSide,transparent:true,depthWrite:false}),gem=new THREE.Mesh(cuts.spindle.geometry,material);
  gem.position.copy(center);gem.quaternion.setFromUnitVectors(V(1,0,0),direction);gem.scale.setScalar(scale);parent.add(gem);
  gem.onBeforeRender=()=>{uniforms.uWorld.value.copy(gem.matrixWorld);uniforms.uInvModel.value.copy(gem.matrixWorld).invert();material.uniformsNeedUpdate=true;};
  for(const sign of [-1,1]){const collar=new THREE.Mesh(new THREE.TorusGeometry(.065*scale,.013,8,20),polished);collar.position.copy(center).addScaledVector(direction,sign*(halfGem-.055*scale));collar.quaternion.setFromUnitVectors(V(0,0,1),direction);parent.add(collar);}
}
function design(kind){
  const group=new THREE.Group(),vertices=[],edges=[];
  if(kind==='dual'){
    vertices.push({color:'#f3ead7',position:V(0,Math.sqrt(2)*1.12,0)},{color:'#10121b',position:V(0,-Math.sqrt(2)*1.12,0)});
    ['#bf392f','#145b37','#174789'].forEach((color,i)=>{const angle=i*2*Math.PI/3+.12;vertices.push({color,position:V(Math.cos(angle)*1.12,0,Math.sin(angle)*1.12)});});
    for(let i=2;i<5;i++){edges.push([0,i],[1,i],[i,2+(i-1)%3]);}
  }else{
    for(let i=0;i<8;i++)vertices.push({color:[0,3,5,6].includes(i)?'#f6efe5':'#e6ca93',position:V(i&1?.97:-.97,i&2?.97:-.97,i&4?.97:-.97)});
    for(let i=0;i<8;i++)for(let j=i+1;j<8;j++)if([1,2,4].includes(i^j))edges.push([i,j]);
  }
  for(const vertex of vertices)pearl(group,vertex);
  const stones=edges.map(([a,b])=>{const tint=kind==='dual'?midpoint(vertices[a].color,vertices[b].color):new THREE.Color('#eedcad');gemstone(group,vertices[a].position,vertices[b].position,tint);return {a,b,color:'#'+tint.getHexString(),linear:tint.toArray()};});
  return {group,vertices,stones};
}
const designs={dual:design('dual'),cube:design('cube')};
for(const d of Object.values(designs))scene.add(d.group);
window.logoStudio={
  designs:Object.fromEntries(Object.entries(designs).map(([key,d])=>[key,{vertices:d.vertices.map(v=>({color:v.color,position:v.position.toArray()})),stones:d.stones}])),
  render(kind,turn=0,size=256){
    if(renderer.domElement.width!==size)renderer.setSize(size,size);
    for(const [name,d]of Object.entries(designs))d.group.visible=name===kind;
    const group=designs[kind].group;group.rotation.set(kind==='cube'?.30:0,turn*Math.PI*2+.58,kind==='cube'?-.12:0);group.updateMatrixWorld(true);
    renderer.render(scene,camera);return renderer.domElement.toDataURL('image/png').split(',')[1];
  }
};
window.logoStudio.render('dual');
