let database;
function open() {
  return database ??= new Promise((resolve,reject)=>{
    const req=indexedDB.open('tetrarchs-forge-v1',1);
    req.onupgradeneeded=()=>req.result.createObjectStore('cards',{keyPath:'id'});
    req.onsuccess=()=>resolve(req.result); req.onerror=()=>reject(req.error);
  });
}
export async function savedCards() {
  const db=await open();
  return new Promise((resolve,reject)=>{
    const req=db.transaction('cards').objectStore('cards').getAll();
    req.onsuccess=()=>resolve(req.result.sort((a,b)=>a.created_at.localeCompare(b.created_at)).slice(-20)); req.onerror=()=>reject(req.error);
  });
}
export async function saveCards(cards) {
  const db=await open();
  return new Promise((resolve,reject)=>{
    const tx=db.transaction('cards','readwrite'), store=tx.objectStore('cards'); store.clear();
    for(const card of cards.slice(-20)) {
      const {illustration,...record}=card; store.put(record);
    }
    tx.oncomplete=resolve; tx.onerror=()=>reject(tx.error); tx.onabort=()=>reject(tx.error||new Error('Local storage failed'));
  });
}
