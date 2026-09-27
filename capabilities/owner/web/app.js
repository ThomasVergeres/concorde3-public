'use strict';
const root=document.querySelector('#app'), notice=document.querySelector('#notice');
let csrf='', level='', route='requests', items=[], offset=null, selected=null, filter='pending', busy=false;
const labels={pending:'Needs you',supplied:'Awaiting verification',verified:'Verified by Concorde',failed:'Verification failed',declined:'Declined',proposed:'Change proposed',cancelled:'Cancelled',expired:'Expired'};
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const button=(label,cls,action)=>{const n=el('button',cls,label);n.type='button';n.addEventListener('click',action);return n;};
const date=t=>new Date(t*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
const say=(text,error=false)=>{notice.textContent=text;notice.className=error?'error':'';if(error&&text){notice.tabIndex=-1;notice.focus();notice.scrollIntoView({block:'center'});}};
const status=s=>el('span','status '+s,labels[s]||s);
const clear=()=>{root.replaceChildren();say('');};
async function api(path,body,method){
  const response=await fetch(path,{method:method||(body===undefined?'GET':'POST'),credentials:'same-origin',
    headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
  let value;try{value=await response.json();}catch{throw new Error('The service returned an unreadable response. Please try again.');}
  if(!response.ok){if(response.status===401){level='';auth();}throw new Error(value.error||'Could not complete the action. Please retry.');}return value;
}
async function action(fn,target){
  if(busy)return;busy=true;if(target)target.disabled=true;say('');
  try{await fn();}catch(e){say(e.name==='NotAllowedError'?'Passkey interaction was cancelled or timed out. You can try again.':e.message,true);}
  finally{busy=false;if(target)target.disabled=false;}
}
function b64(bytes){let s='';for(const b of new Uint8Array(bytes))s+=String.fromCharCode(b);return btoa(s).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
function unb64(s){return Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));}
function credentialJSON(c){
  const response={clientDataJSON:b64(c.response.clientDataJSON)};
  for(const k of ['attestationObject','authenticatorData','signature','userHandle'])if(c.response[k])response[k]=b64(c.response[k]);
  if(c.response.getTransports)response.transports=c.response.getTransports();
  return {id:c.id,rawId:b64(c.rawId),type:c.type,response,clientExtensionResults:c.getClientExtensionResults()};
}
async function passkey(register=false){
  if(!window.PublicKeyCredential)throw new Error('Passkeys are not available here. Open this HTTPS page in Safari on your iPhone.');
  const base='/auth/'+(register?'register':'login');
  const data=await api(base+'/options',{}), options=data.options;
  options.challenge=unb64(options.challenge);
  if(options.user)options.user.id=unb64(options.user.id);
  for(const key of ['allowCredentials','excludeCredentials'])if(options[key])options[key]=options[key].map(x=>({...x,id:unb64(x.id)}));
  const credential=register?await navigator.credentials.create({publicKey:options}):await navigator.credentials.get({publicKey:options});
  if(!credential)throw new Error('No passkey returned. Please try again.');
  const result=await api(base+'/verify',{challenge_id:data.challenge_id,credential:credentialJSON(credential),label:'Owner passkey'});
  csrf=result.csrf;level=result.level;document.querySelector('#signout').hidden=false;
  await load();
}
function auth(){
  clear();document.querySelector('#signout').hidden=true;
  const section=el('section','auth'),mark=el('img','brandmark');mark.src='/assets/mark.svg';mark.alt='';
  section.append(mark,el('h1','',level==='recover'?'Recover your owner access':level==='enroll'?'Your inbox, secured.':'A small handoff.\nThen back to your day.'));
  const enrollment=level==='enroll'||level==='recover';
  section.append(el('p','',enrollment?(level==='recover'?'Create a replacement passkey. This signs out existing sessions and replaces your previous passkeys.':'Create a passkey to open your Concorde inbox using Face ID, Touch ID, or your device unlock.'):'Review decisions, share credentials securely, and unblock your Concordes. No server console needed.'));
  const actions=el('div','actions');
  const sign=button(enrollment?'Create passkey':'Sign in with passkey','primary',()=>action(()=>passkey(enrollment),sign));actions.append(sign);section.append(actions);
  if(!enrollment){const details=el('details');details.append(el('summary','','First time here, or lost your passkey?'),el('p','','Send a private setup or recovery link to the configured owner email. A recovery link only lets you enroll a passkey; it does not expose credentials.'));
    const mail=button('Email access link','secondary',()=>action(async()=>{await api('/auth/email',{});say('Check your owner email. The link is valid for 15 minutes.');},mail));details.append(mail);section.append(details);}
  section.append(el('p','disclosure','Credential values are never displayed in this inbox after submission. Keep access links private.'));
  root.append(section);
}
function nav(){const n=el('nav','nav');n.setAttribute('aria-label','Owner workspace');for(const [key,title] of [['requests','Requests'],['operations','Dependencies']]){
  const b=button(title,'',()=>action(async()=>{route=key;selected=null;history.replaceState(null,'','/');await load();}));b.setAttribute('aria-current',route===key?'page':'false');n.append(b);}return n;}
async function load(append=false){
  if(level!=='owner'){auth();return;}
  if(selected){await detail(selected);return;}
  const path=route==='requests'?'/api/requests':'/api/operations';
  const result=await api(path+'?offset='+(append?(offset||0):0));items=append?items.concat(result.items):result.items;offset=result.next_offset;renderList();
}
function renderList(){
  clear();const heading=el('div','heading'),intro=el('div');intro.append(el('h1','',route==='requests'?'Requests':'Dependencies'),el('p','',route==='requests'?'The decisions and access only you can provide.':'What each Concorde relies on. Recorded state, not a live service guarantee.'));heading.append(intro);
  const refresh=button('Refresh','quiet',()=>action(()=>load(),refresh));heading.append(refresh);root.append(heading,nav());
  if(route==='operations'){for(const i of items){const d=el('article','dependency');d.append(el('h2','',i.name),el('p','',i.instance_name+' · '+i.state.replaceAll('_',' ')),el('p','',i.purpose));
    const money=new Intl.NumberFormat(undefined,{style:'currency',currency:i.currency});const units=10**money.resolvedOptions().maximumFractionDigits;
    const facts=el('dl');for(const [name,value] of [['Recorded cost',money.format(i.recurring_minor/units)+' / '+i.period],['Next review',i.next_review?date(i.next_review):'Not recorded']]){const group=el('div');group.append(el('dt','',name),el('dd','',value));facts.append(group);}d.append(facts);root.append(d);}
  }else{
    const filters=el('div','filter'),label=el('label','','Show');label.htmlFor='filter';const select=el('select');select.id='filter';
    for(const [key,text] of [['pending','Needs you'],['all','All requests'],['supplied','Awaiting verification']]){const op=el('option','',text);op.value=key;select.append(op);}select.value=filter;select.onchange=()=>{filter=select.value;renderList();};filters.append(label,select);root.append(filters);
    const visible=items.filter(i=>filter==='all'||i.state===filter),list=el('div','request-list');
    for(const i of visible){const row=button('','request-row',()=>action(async()=>{selected=i.id;history.pushState(null,'','/#request='+i.id);await detail(i.id);document.querySelector('#main').focus();}));row.append(el('span','company',i.instance_name),el('strong','',i.title),el('span','meta',(i.state==='pending'?'Respond by ':'Requested ')+date(i.state==='pending'?i.deadline:i.created)),status(i.state));list.append(row);}root.append(list);
    if(!visible.length){const empty=el('section','empty');empty.append(el('h2','',filter==='pending'?'Nothing here needs you right now.':'No matching requests.'),el('p','',offset!==null?'There are more records to load below.':'When a Concorde needs a specific decision or handoff, it will appear here.'));root.append(empty);}
  }
  if(route==='operations'&&!items.length){const empty=el('section','empty');empty.append(el('h2','','No dependencies recorded.'),el('p','','Concordes can record their accounts, service dependencies, renewal dates and billing evidence through the capability.'));root.append(empty);}
  if(offset!==null){const more=button('Load more','secondary load-more',()=>action(()=>load(true),more));root.append(more);}
}
function fact(dl,name,value){const group=el('div');group.append(el('dt','',name),el('dd','',value));dl.append(group);}
async function detail(id){
  const r=await api('/api/requests/'+encodeURIComponent(id));clear();
  root.append(button('Back to requests','quiet back',()=>action(async()=>{selected=null;route='requests';history.pushState(null,'','/');await load();})));
  const title=el('div','detail-title');title.append(el('h1','',r.title));const by=el('div','byline');by.append(el('span','',r.instance_name),status(r.state));title.append(by);root.append(title);
  const layout=el('div','detail-layout'),main=el('section'),facts=el('dl','facts');
  fact(facts,'Why this is needed',r.reason);fact(facts,'Scope',r.scope_text);fact(facts,'While waiting',r.fallback);fact(facts,'Respond by',date(r.deadline));main.append(facts);
  if(r.state==='pending')main.append(responseForm(r));else main.append(receipt(r));
  const rail=el('aside','rail');rail.append(el('h2','','Handoff progress'));const timeline=el('ol','timeline');
  const terminal=['declined','proposed','cancelled','expired'].includes(r.state);
  const steps=terminal?[['Requested',true,date(r.created)],[labels[r.state],true,r.response.at?date(r.response.at):'No approval granted']]:[['Requested',true,date(r.created)],['Supplied',!!r.response.at,r.response.at?date(r.response.at):'Your action'],[r.state==='failed'?'Verification failed':'Verified',r.state==='verified'||r.state==='failed',r.state==='failed'?'Reported failure':'Concorde checks the result']];
  for(const [name,done,note] of steps){const li=el('li',done?'done':'');li.append(el('span','',done?name+' · Recorded':name==='Supplied'?'Awaiting your response':'Awaiting verification'),el('small','',note));timeline.append(li);}rail.append(timeline,el('p','hint','An answer is not a blanket approval. Your response applies only to the scope shown here.'));layout.append(main,rail);root.append(layout);
  if(r.state!=='pending'){const heading=main.querySelector('.receipt h2');heading.tabIndex=-1;heading.focus();heading.scrollIntoView({block:'center'});}
}
function responseForm(r){
  const form=el('form','action-section'),key=crypto.randomUUID();let input=null,file=null;
  if(r.kind==='credential'){
    form.append(el('h2','','Share securely'));const field=el('div','field first-field');const label=el('label','','Credential');label.htmlFor='credential';input=el('input');input.type='password';input.id='credential';input.autocomplete='off';input.autocapitalize='none';input.spellcheck=false;input.maxLength=65536;field.append(label,input,el('p','hint','Paste a key, password, or code. It is sent only to this Concorde.'));form.append(field);
    const f=el('div','field');const fl=el('label','','Or upload a credential file');fl.htmlFor='credential-file';file=el('input');file.type='file';file.id='credential-file';f.append(fl,file,el('p','hint','Maximum 1 MiB. Choose a file or enter text, not both.'));form.append(f,el('p','hint',(r.single_use?'Single-use retrieval. ':'')+'Available to Concorde for '+Math.round(r.secret_ttl/60)+' minutes after submission.'));
  }else if(r.kind==='answer'){
    const label=el('label','','Your answer');label.htmlFor='answer';input=el('textarea');input.id='answer';input.maxLength=6000;input.required=true;form.append(label,input,el('p','hint','Do not include credentials here. Ask for a secure handoff instead.'));
  }else if(r.kind==='external'){
    form.append(el('h2','','Complete the external step'));if(r.action_url){const a=el('a','external','Open '+new URL(r.action_url).hostname);a.href=r.action_url;a.target='_blank';a.rel='noopener noreferrer';form.append(a,el('p','hint','This link was supplied by the Concorde. Check the provider and requested permissions before continuing.'));}form.append(el('p','hint','Return here after completing the step. Concorde will independently check whether access works.'));
  }else {form.append(el('h2','','Your decision'),el('p','hint','Approve only the exact scope above. Nothing is purchased by this button itself.'));const details=el('details','scope-change');details.append(el('summary','','Want a different scope?'));const label=el('label','','Proposed change');label.htmlFor='proposed-change';const proposal=el('textarea');proposal.id='proposed-change';proposal.maxLength=6000;const propose=button('Send proposed change','secondary',()=>action(async()=>{if(!proposal.value.trim())throw new Error('Describe the change you want.');await api('/api/requests/'+r.id+'/respond',{key,scope:r.scope,action:'propose',note:proposal.value});await detail(r.id);},propose));details.append(label,proposal,el('p','hint','A proposal is not approval. Concorde must obtain approval for any new terms.'),propose);form.append(details);}
  const actions=el('div','actions'),names={decision:['Approve scope','approve'],answer:['Send answer','answer'],credential:['Share securely','supply'],external:['I completed this step','done']};
  const send=button(names[r.kind][0],'primary',()=>{});send.type='submit';
  const decline=button('Decline','secondary danger',()=>action(async()=>{await api('/api/requests/'+r.id+'/respond',{key,scope:r.scope,action:'decline'});if(input)input.value='';if(file)file.value='';await detail(r.id);},decline));actions.append(send,decline);form.append(actions,el('p','secure-note','Your response is saved durably. Concorde resumes when eligible and verifies the result.'));
  form.onsubmit=e=>{e.preventDefault();action(async()=>{
    const payload={key,scope:r.scope,action:names[r.kind][1]};
    if(r.kind==='answer')payload.note=input.value;
    if(r.kind==='credential'){
      if(file.files.length&&input.value)throw new Error('Choose a file or text, not both.');
      if(file.files.length){const f=file.files[0];if(f.size>1048576)throw new Error('The file must be 1 MiB or smaller.');payload.secret=btoa(Array.from(new Uint8Array(await f.arrayBuffer()),x=>String.fromCharCode(x)).join(''));payload.filename=f.name;payload.encoding='base64';}
      else payload.secret=input.value;
      if(!payload.secret)throw new Error('Enter a credential or choose a file.');
    }
    await api('/api/requests/'+r.id+'/respond',payload);
    if(input)input.value='';if(file)file.value='';payload.secret='';await detail(r.id);
  },send);};return form;
}
function receipt(r){
  const section=el('section','receipt '+(r.state==='failed'?'failed':''));
  const titles={supplied:'Your part is done.',verified:'Result verified.',failed:'The handoff needs follow-up.',declined:'Request declined.',proposed:'Change proposed—not approved.',cancelled:'Request cancelled.',expired:'This request has expired.'};
  section.append(el('h2','',titles[r.state]||r.state));
  const messages={supplied:'Your response is saved. Concorde has an observation waiting to be delivered and will check the result when it can run. This does not yet mean the dependency works.',verified:'The requesting Concorde reports that it checked the result successfully. Its evidence is below.',failed:'Concorde reports that verification did not succeed. Your submitted response is preserved; it may request a new handoff.',declined:'Concorde can continue with its fallback or reconsider the approach.',cancelled:'The requesting Concorde no longer needs this action.',expired:'No new response can be submitted. Concorde can request a fresh handoff if it is still needed.'};
  section.append(el('p','',r.state==='proposed'?'Your proposed change is saved. This grants no authority; Concorde must submit a new request for changed terms.':messages[r.state]||''));
  if(r.verification)section.append(el('pre','',r.verification.evidence));
  if(r.response.note)section.append(el('pre','',r.response.note));
  if(r.response.credential){section.append(el('p','hint','Credential value hidden. Retrieval expires '+date(r.response.expires)+'. Revocation stops future retrieval, not copies already obtained.'));const revoke=button('Revoke credential access','secondary danger',()=>action(async()=>{await api('/api/credentials/'+r.response.credential+'/revoke',{});say('Credential revoked. Copies already retrieved are not erased.');revoke.hidden=true;},revoke));const controls=el('div','actions');controls.append(revoke);section.append(controls);}
  const refresh=button('Check for verification','quiet',()=>action(()=>detail(r.id),refresh));section.append(refresh);return section;
}
document.querySelector('#signout').onclick=()=>action(async()=>{await api('/auth/logout',{});csrf='';level='';items=[];selected=null;history.replaceState(null,'','/');auth();});
window.addEventListener('popstate',()=>{const hash=new URLSearchParams(location.hash.slice(1));if(hash.has('setup')){start();return;}selected=hash.get('request');action(()=>load());});
async function start(){
  const hash=new URLSearchParams(location.hash.slice(1)),setup=hash.get('setup');selected=hash.get('request');
  if(setup){history.replaceState(null,'','/');try{const result=await api('/auth/redeem',{token:setup});csrf=result.csrf;level=result.level;auth();}catch(e){auth();say(e.message,true);}return;}
  try{const value=await api('/auth/session');csrf=value.csrf;level=value.level;document.querySelector('#signout').hidden=level!=='owner';await load();}
  catch(e){auth();if(!e.message.includes('Sign in'))say(e.message,true);}
}
start();
