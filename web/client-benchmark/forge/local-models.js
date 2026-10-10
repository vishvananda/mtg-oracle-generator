// Reuse the benchmark's exact model URLs and cache namespaces.
import {revisionMessages} from './revision-prompt.js';
import { Wllama } from '/model-bench/vendor/wllama/index.js';
import { isIOS, mobileModelMessage } from '/model-bench/device-support.js';
const base = new URL('/model-bench/',location.origin);
const url = path => new URL(path,base).href;
const randomSeed=()=>crypto.getRandomValues(new Uint32Array(1))[0]>>>1;
export class LocalModels {
  engine=null; textReady=false; imageReady=false; worker=null; pending=null; controller=null;
  get ready(){return this.textReady && this.imageReady;}
  async load(report) {
    if(isIOS)throw new Error(mobileModelMessage);
    if(!navigator.gpu || !await navigator.gpu.requestAdapter()) throw new Error('Local generation needs WebGPU. Open this page in a current Chrome or Edge browser on a supported device. You can still explore and edit the example cards.');
    if(!crossOriginIsolated) throw new Error('This page needs browser isolation headers to load the models. Please reload.');
    if(!this.textReady) {
      [this.manifest,this.systemPrompt]=await Promise.all([fetch(url('model.json')).then(r=>{if(!r.ok)throw Error('Card model configuration could not load.');return r.json();}),fetch(url('system-prompt.txt')).then(r=>{if(!r.ok)throw Error('Card instructions could not load.');return r.text();})]);
      this.engine=new Wllama({default:url('vendor/wllama/wasm/wllama.wasm')},{parallelDownloads:3,logger:{debug(){},log(){},warn:console.warn,error:console.error}});
      this.engine.setCompat({wasm:url('vendor/compat/wllama.wasm'),worker:url('vendor/compat/wllama.js')});
      try {
        report('Loading the card model…',.05);
        const model=await this.engine.modelManager.getModelOrDownload({url:url(this.manifest.first_shard)},{progressCallback:({loaded,total})=>report(`Card model · ${(loaded/1e9).toFixed(1)} / ${(total/1e9).toFixed(1)} GB`,total ? .36*loaded/total : .05)});
        report('Preparing the card model on your device…',.37);
        await this.engine.loadModel(model,{n_ctx:4096,n_threads:Math.min(8,Math.max(1,Math.floor((navigator.hardwareConcurrency||2)/2))),n_gpu_layers:99,n_batch:256,n_ubatch:128,n_parallel:1,jinja:true,default_template_kwargs:{enable_thinking:false}});
        this.textReady=true;
      } catch(error) {try{await this.engine.exit();}catch{} this.engine=null;throw error;}
    }
    if(!this.imageReady) {
      report('Loading the art model…',.4);
      this.worker=new Worker(url('bonsai-worker.js'),{type:'module'});
      this.worker.onmessage=({data})=>{
        if(data.type==='ready') this.imageReady=true;
        if(data.type==='error'||data.type==='device-lost') {
          this.imageReady=false; this.worker?.terminate(); this.worker=null;
          this.pending?.reject(new Error(data.message)); this.pending=null; return;
        }
        if(['ready','image'].includes(data.type)) {this.pending?.resolve(data);this.pending=null;}
        else this.pending?.progress(data);
      };
      this.worker.onerror=event=>{event.preventDefault();this.imageReady=false;this.worker?.terminate();this.worker=null;this.pending?.reject(new Error(event.message||'The art worker failed.'));this.pending=null;};
      const component={text_encoder:0,transformer:0,vae:0};
      await this.request({type:'load'},data=>{
        if(data.type==='progress' && data.progress.component) {
          const p=data.progress;
          if(p.total) component[p.component]=Math.min(1,p.loaded/p.total);
          const fraction=(component.text_encoder*2.263+component.transformer*1.425+component.vae*.168)/3.856;
          report(`${p.fromCache?'Reading cached':'Loading'} art model…`,.4+.58*fraction);
        } else if(data.type==='stage') report(data.message,.42);
      });
    }
    report('Your studio is ready.',1);
  }
  request(message,progress=()=>{}) {
    return new Promise((resolve,reject)=>{if(!this.worker){reject(new Error('Load the models before generating artwork.'));return;} this.pending={resolve,reject,progress};this.worker.postMessage(message);});
  }
  async card(description,report) {return this.complete([{role:'system',content:this.systemPrompt},{role:'user',content:description}],report,.7);}
  async revise(card,change,report,diagnostic=null){return this.complete(revisionMessages(this.systemPrompt,card,change,diagnostic),report,.2);}
  async complete(messages,report,temperature) {
    if(!this.textReady) throw new Error('Load the models first.');
    this.controller=new AbortController(); const signal=this.controller.signal, seed=randomSeed(); let text='';
    try {
      await this.engine.createChatCompletion({messages,max_tokens:768,temperature,top_p:.8,top_k:20,min_p:0,seed,cache_prompt:true,chat_template_kwargs:{enable_thinking:false},stream:true,return_progress:true,abortSignal:signal,
        onData:chunk=>{const delta=chunk.choices?.[0]?.delta?.content;if(delta){text+=delta;report('Writing your card…',text);}else if(chunk.prompt_progress)report('Imagining your card…',text);}});
      if(signal.aborted) throw new DOMException('Generation stopped.','AbortError');
      return {text,seed};
    } finally {this.controller=null;}
  }
  async art(prompt,report) {
    if(!this.imageReady)throw new Error('Load the art model before generating artwork.');
    const seed=randomSeed();
    const result=await this.request({type:'generate',prompt,width:768,height:512,steps:4,seed},data=>{
      if(data.type==='step')report(`Painting your artwork · ${data.completed} / ${data.total}`);
      else if(data.type==='stage') {
        if(data.message==='text encode')report('Imagining the scene…');
        else if(data.message==='vae decode')report('Bringing the artwork into focus…');
        else if(data.message.startsWith('step '))report('Painting your artwork…');
      }
    });
    return {blob:result.blob,seed,total_ms:result.generation_ms,width:768,height:512,steps:4};
  }
  cancel() {
    this.controller?.abort();
    if(this.pending) {this.worker?.terminate();this.worker=null;this.imageReady=false;this.pending.reject(new DOMException('Generation stopped.','AbortError'));this.pending=null;}
  }
  dispose(){this.cancel();this.worker?.terminate();this.engine?.exit().catch(()=>{});}
}
