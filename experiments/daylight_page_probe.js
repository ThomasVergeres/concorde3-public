// Narrow execution of the archived page's real selection logic. This is a DOM
// model, not a browser/layout/accessibility or customer-enjoyment test.
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.textContent=''; this.value='5'; this.classList={toggle(){}}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; }
  addEventListener(name, fn) { this['on'+name]=fn; }
  focus() {}
}
const elements = new Map();
const document = {getElementById(id) {if (!elements.has(id)) elements.set(id,new Element()); return elements.get(id);},createElement(tag){return new Element(tag);}};
const storage = new Map();
let fetched = false;
const context=vm.createContext({document,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  fetch:async path=>{if(path!='/api/catalog')throw Error('Unexpected page dependency '+path); fetched=true; return {ok:true,json:async()=>input.catalog};},
  confirm:()=>false,setTimeout,URL,Blob});
const scripts=[...input.html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)];
if(scripts.length!==1)throw Error('Unqualified page script inventory');
vm.runInContext(scripts[0][1],context,{timeout:1000});
setImmediate(()=>{
  if(!fetched)throw Error('Page did not obtain catalog');
  const track=input.catalog.tracks.find(t=>t.id===input.track);
  if(!track)throw Error('Promised track absent');
  const tile=document.getElementById('tracks').children.find(el=>el.children.some(c=>c.tag==='h3'&&c.textContent===track.title));
  if(!tile)throw Error('No visible selection for promised track');
  const button=tile.children.find(c=>c.tag==='button');
  if(!button?.onclick)throw Error('No working selection');
  if(input.mode!=='initial')button.onclick();
  const result={track:track.id,title:document.getElementById('sessionTitle').textContent,prompt:document.getElementById('quick').textContent};
  if(result.title!==track.cards[0].title || result.prompt!==track.cards[0].quick)throw Error('Selection reached wrong first card');
  console.log(JSON.stringify(result));
});
