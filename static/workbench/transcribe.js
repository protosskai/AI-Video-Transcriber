import {api} from './api.js';
import {renderMarkdown} from './markdown.js';

const $=s=>document.querySelector(s);
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
const button=(text,fn,cls)=>{const b=node('button',text,cls);b.type='button';b.onclick=fn;return b;};
const labels={queued:'排队中',running:'转录中',completed:'已完成',failed:'失败',interrupted:'已中断',cancelled:'已取消'};
const kinds={raw:'原文',script:'整理稿',summary:'摘要',translation:'翻译'};
const active=j=>['queued','running'].includes(j.status);
const date=t=>t?new Date(t).toLocaleDateString('zh-CN',{month:'short',day:'numeric'}):'';
const session={get(k){try{return sessionStorage.getItem('transcribe:'+k);}catch{return null;}},set(k,v){try{sessionStorage.setItem('transcribe:'+k,v);}catch{}},remove(k){try{sessionStorage.removeItem('transcribe:'+k);}catch{}}};
let params=new URLSearchParams(location.search),selected=params.get('content')||params.get('task'),run=params.get('run')||'';
let kind='raw',view=['archive','trash'].includes(params.get('view'))?params.get('view'):'library',filter='',query='',page=1;
let current=null,busy=false,pending=false,actionBusy=false,editing=false,submitting=false,file=null;
let signature='',listSignature='',recentSignature='',formKey=session.get('form-key')||crypto.randomUUID(),toastTimer;

function toast(text){clearTimeout(toastTimer);$('#toast').textContent=text;$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,2600);}
function error(text,where='#action-error'){$(where).textContent=text;$(where).hidden=false;}
const mobile=matchMedia('(max-width:760px)');
function syncSidebar(){const opened=document.body.classList.contains('sidebar-open');$('#sidebar').inert=mobile.matches&&!opened;$('.main-shell').inert=mobile.matches&&opened;$('#open-sidebar').setAttribute('aria-expanded',String(!$('#sidebar').inert));}
mobile.addEventListener('change',syncSidebar);syncSidebar();
function sidebar(open){document.body.classList.toggle('sidebar-open',open);$('#sidebar-backdrop').hidden=!open;syncSidebar();if(open)$('#search').focus();else $('#open-sidebar').focus();}
$('#open-sidebar').onclick=()=>sidebar(true);$('#close-sidebar').onclick=$('#sidebar-backdrop').onclick=()=>sidebar(false);
function locationUpdate(replace=false){const u=new URL(location.href);u.search='';if(selected)u.searchParams.set('content',selected);if(run)u.searchParams.set('run',run);if(view!=='library')u.searchParams.set('view',view);history[replace?'replaceState':'pushState']({},'',u);}
function open(id,version='',push=true){
  if(submitting)return;
  selected=id;run=version;kind='raw';current=null;editing=false;signature='';listSignature='';
  $('#start').hidden=!!id;$('#result').hidden=!id;$('#action-error').hidden=true;
  document.body.classList.remove('sidebar-open');$('#sidebar-backdrop').hidden=true;syncSidebar();
  if(id)$('#result-content').replaceChildren(node('p','正在读取…','empty-result'));
  if(push)locationUpdate();window.scrollTo(0,0);refresh();
  if(!id)$('#video-url').focus();
}
$('#new-transcript').onclick=$('#mobile-new').onclick=()=>open(null);
$('#history-view').value=view;$('#history-view').onchange=e=>{view=e.target.value;page=1;listSignature='';locationUpdate(true);refresh();};
$('#status-filter').onchange=e=>{filter=e.target.value;page=1;refresh();};
let searchTimer;$('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{query=$('#search').value;page=1;refresh();},200);};
$('#previous').onclick=()=>{page--;refresh();};$('#next').onclick=()=>{page++;refresh();};

function renderList(data){
  $('#page-number').textContent=`${page} / ${Math.max(1,Math.ceil(data.total/data.size))}`;
  $('.pagination').hidden=data.total<=data.size;$('#previous').disabled=page===1;$('#next').disabled=page*data.size>=data.total;
  const hash=JSON.stringify(data.items)+selected;if(hash===listSignature)return;listSignature=hash;
  const list=document.createDocumentFragment();
  for(const c of data.items){const b=button('',()=>open(c.id),'history-item'+(selected===c.id?' selected':''));b.title=c.title+' · '+labels[c.job.status];b.setAttribute('aria-label',b.title);b.setAttribute('aria-current',String(c.id===selected));b.append(node('span',null,'dot '+c.job.status),node('span',c.title,'history-title'));list.append(b);}
  if(!data.items.length)list.append(node('p',query||filter?'没有匹配的转录':view==='trash'?'回收站为空':view==='archive'?'暂无归档':'还没有转录记录','quiet'));
  $('#history-list').replaceChildren(list);
}
function renderRecent(data){
  const hash=JSON.stringify(data.items.slice(0,3));if(hash===recentSignature)return;recentSignature=hash;
  $('#recent').hidden=!data.items.length;
  const rows=data.items.slice(0,3).map(c=>{const b=button('',()=>open(c.id),'recent-item');b.append(node('span',null,'dot '+c.job.status),node('span',c.title,'recent-title'),node('span',labels[c.job.status],'state'),node('time',date(c.created_at)));return b;});
  $('#recent-list').replaceChildren(...rows);
}
function confirmAction(title,description,accept){
  const dialog=$('#confirm-dialog');$('#confirm-title').textContent=title;$('#confirm-description').textContent=description;$('#confirm-accept').textContent=accept;dialog.returnValue='';dialog.showModal();
  return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='accept'),{once:true}));
}
function menu(title,aria){const d=node('details',null,'menu'),s=node('summary',title);s.setAttribute('aria-label',aria);d.append(s,node('div',null,'menu-panel'));return d;}
document.addEventListener('click',e=>document.querySelectorAll('.menu[open],.options[open]').forEach(d=>{if(!d.contains(e.target))d.open=false;}));
document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.querySelectorAll('.menu[open],.options[open]').forEach(d=>{d.open=false;d.querySelector('summary').focus();});if(document.body.classList.contains('sidebar-open'))sidebar(false);}});
async function copy(text,b){
  try{await navigator.clipboard.writeText(text);const label=b.textContent;b.textContent='已复制 ✓';b.setAttribute('aria-live','polite');setTimeout(()=>{if(b.isConnected)b.textContent=label;},1800);}
  catch{error('无法访问剪贴板，请选择文字复制，或下载文稿。');}
}
async function mutate(c,action,b,title){
  if(actionBusy)return false;
  actionBusy=true;$('#action-error').hidden=true;const label=b.textContent;b.disabled=true;b.textContent='保存中…';
  try{await api(`/library/${c.id}/action`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,title})});signature='';listSignature='';toast({rename:'标题已更新',archive:'已归档',unarchive:'已移回最近转录',trash:'已移入回收站',restore:'已恢复',cancel:'已取消排队'}[action]);return true;}
  catch(e){error(e.message);return false;}
  finally{actionBusy=false;b.disabled=false;b.textContent=label;refresh();}
}
async function retry(c,b){
  if(actionBusy)return;
  if(c.job.status==='completed'&&!await confirmAction('重新转录？','将重新获取音视频并调用模型。已有文稿会保留。','重新转录'))return;
  actionBusy=true;$('#action-error').hidden=true;const original=b.textContent;b.disabled=true;b.textContent='正在提交…';
  const keyName='retry:'+c.id,key=session.get(keyName)||crypto.randomUUID();session.set(keyName,key);
  try{const data=await api(`/library/${c.id}/retry`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_key:key})});session.remove(keyName);if(selected===c.id){run='';locationUpdate(true);}signature='';listSignature='';toast(data.reused?'已在队列中':'已重新排队');}
  catch(e){error(e.message+' 再次重试不会重复排队。');}
  finally{actionBusy=false;b.disabled=false;b.textContent=original;refresh();}
}
function rename(c,heading){
  editing=true;const f=node('form',null,'rename-form'),input=node('input');input.value=c.title;input.maxLength=300;input.required=true;input.setAttribute('aria-label','转录标题');
  const save=node('button','保存','primary');save.type='submit';const cancel=button('取消',()=>{editing=false;signature='';renderDetail(current);});
  const err=node('p',null,'form-error');err.setAttribute('role','alert');err.hidden=true;f.append(input,save,cancel,err);heading.replaceWith(f);input.focus();input.select();
  f.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();cancel.click();}};
  f.onsubmit=async e=>{e.preventDefault();if(!input.value.trim()){err.textContent='标题不能为空';err.hidden=false;return;}input.disabled=cancel.disabled=true;const ok=await mutate(c,'rename',save,input.value.trim());input.disabled=cancel.disabled=false;if(ok){editing=false;signature='';refresh();}else{err.textContent=$('#action-error').textContent;err.hidden=false;$('#action-error').hidden=true;input.focus();}};
}
function renderDetail(c){
  current=c;if(editing||actionBusy||$('#result-content .menu[open]'))return;
  const hash=JSON.stringify(c)+kind;if(hash===signature)return;signature=hash;
  const root=$('#result-content'),j=c.job,reading=c.reading,fragment=document.createDocumentFragment();
  const nav=node('div',null,'result-nav');nav.append(button('← 新转录',()=>open(null),'back'));
  const more=menu('···','更多操作'),panel=more.querySelector('.menu-panel');
  panel.append(button('复制链接',e=>copy(location.origin+'/workbench/?content='+c.id,e.currentTarget)));
  const heading=node('h1',c.title);
  panel.append(button('重命名',()=>{more.open=false;rename(c,heading);}));
  if(c.trashed)panel.append(button('恢复',e=>mutate(c,'restore',e.currentTarget)));
  else if(c.archived)panel.append(button('取消归档',e=>mutate(c,'unarchive',e.currentTarget)));
  else if(!active(j)){
    if(!j.imported)panel.append(button('重新转录',e=>{more.open=false;retry(c,e.currentTarget);}));
    panel.append(button('归档',e=>{more.open=false;mutate(c,'archive',e.currentTarget);}),button('移入回收站',async e=>{more.open=false;const b=e.currentTarget;if(await confirmAction('移入回收站？','文稿会保留，之后可以从回收站恢复。','移入回收站'))mutate(c,'trash',b);},'danger'));
  }
  nav.append(more);fragment.append(nav);
  const meta=node('div',null,'result-meta');
  if(/^https?:\/\//.test(j.source)){const a=node('a',new URL(j.source).hostname.replace(/^www\./,''));a.href=j.source;a.target='_blank';a.rel='noopener noreferrer';meta.append(a);}else meta.append(node('span',j.source));
  meta.append(node('span','·'),node('time',date(c.created_at)));if(c.archived||c.trashed)meta.append(node('span',c.trashed?'回收站':'已归档'));
  fragment.append(meta,heading);
  if(j.status!=='completed'){
    const stage=node('div',null,'stage'),line=node('div',null,'stage-line');let title=j.stage;
    if(j.status==='queued')title=`等待转录 · 前面还有 ${Math.max(0,j.queue_position-1)} 个任务`;
    if(j.status==='failed')title=/下载|SSL|HTTP/.test(j.error||j.stage)?'暂时无法获取这段音视频':'这次转录没有完成';
    if(j.status==='interrupted')title='转录已中断';if(j.status==='cancelled')title='已取消排队';
    line.append(node('span',null,'dot '+j.status),node('strong',title));stage.append(line);
    stage.append(node('p',active(j)?'可以离开这个页面，转录会在后台继续。':reading.available.length?'已有文稿已保留。':'可以重试，或上传音视频文件。'));
    if(j.status==='queued')stage.append(button('取消排队',e=>mutate(c,'cancel',e.currentTarget),'subtle'));
    else if(!active(j)&&!j.imported&&!c.trashed&&!c.archived)stage.append(button('重试',e=>retry(c,e.currentTarget),'primary'));
    if(j.error){const d=node('details');d.append(node('summary','查看错误详情'),node('p',j.error));stage.append(d);}fragment.append(stage);
  }
  const available=Object.keys(kinds).filter(k=>reading.result?.[k]);
  if(available.length){
    if(!available.includes(kind))kind=available[0];
    if(reading.id!==j.id)fragment.append(node('p','显示上次转录的文稿','provenance'));
    const toolbar=node('div',null,'document-toolbar'),tabs=node('div',null,'tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','文稿版本');
    const choose=k=>{kind=k;signature='';renderDetail(current);$('#tab-'+k)?.focus();};
    available.forEach((k,i)=>{const b=button(kinds[k],()=>choose(k));b.id='tab-'+k;b.setAttribute('role','tab');b.setAttribute('aria-selected',String(k===kind));b.setAttribute('aria-controls','transcript-panel');b.tabIndex=k===kind?0:-1;b.onkeydown=e=>{let idx=i;if(e.key==='ArrowRight')idx=(i+1)%available.length;else if(e.key==='ArrowLeft')idx=(i+available.length-1)%available.length;else if(e.key==='Home')idx=0;else if(e.key==='End')idx=available.length-1;else return;e.preventDefault();choose(available[idx]);};tabs.append(b);});
    const actions=node('div',null,'document-actions'),text=reading.result[kind];actions.append(button('复制',e=>copy(text,e.currentTarget),'subtle'));
    const download=menu('下载 ↓','下载文稿');download.classList.add('download');for(const fmt of ['txt','md']){const a=node('a',fmt==='txt'?'纯文本 (.txt)':'Markdown (.md)');a.href=`/api/workbench/tasks/${reading.id}/export/${kind}?format=${fmt}`;a.download='';download.querySelector('.menu-panel').append(a);}actions.append(download);toolbar.append(tabs,actions);fragment.append(toolbar);
    const article=node('article',null,'prose');article.id='transcript-panel';article.setAttribute('aria-label',kinds[kind]);const rendered=renderMarkdown(text);
    // Avoid repeating the exact same title directly below the page heading.
    const first=rendered.firstElementChild;if(first?.tagName==='H1'&&first.textContent.trim()===c.title.trim())first.remove();article.append(rendered);fragment.append(article);
  }else if(j.status==='completed')fragment.append(node('p','没有可显示的文稿','empty-result'));
  const history=node('details',null,'attempts');history.append(node('summary',c.attempt_count>1?`转录记录 · ${c.attempt_count} 次`:'转录记录'));
  c.attempts.forEach((attempt,i)=>{const row=node('div',null,'attempt-row');row.append(node('span',`第 ${c.attempt_count-i} 次`),node('span',date(attempt.created_at)),node('span',labels[attempt.status]));if(attempt.available.length)row.append(button(attempt.id===reading.id?'当前文稿':'查看文稿',()=>{run=attempt.id;locationUpdate();signature='';refresh();}));history.append(row);});
  if(run)history.append(button('返回最新文稿',()=>{run='';locationUpdate();signature='';refresh();}));
  const log=node('ol',null,'attempt-log');for(const e of j.events||[])log.append(node('li',e.stage));history.append(log);fragment.append(history);
  const expanded=root.querySelector('.attempts')?.open;root.replaceChildren(fragment);if(expanded)root.querySelector('.attempts').open=true;
}
async function refresh(){
  if(busy){pending=true;return;}busy=true;const state=[selected,run,view,query,filter,page].join('|'),id=selected,version=run;
  try{
    const data=await api(`/library?q=${encodeURIComponent(query)}&status=${filter}&view=${view}&page=${page}`);
    if(state!==[selected,run,view,query,filter,page].join('|')){pending=true;return;}
    if(page>1&&!data.items.length){page--;pending=true;return;}renderList(data);
    if(!id){const recent=view==='library'&&!query&&!filter?data:await api('/library?size=3');if(!selected)renderRecent(recent);}
    else{const c=await api(`/library/${encodeURIComponent(id)}${version?'?run='+encodeURIComponent(version):''}`);if(id===selected&&version===run){if(c.id!==selected){selected=c.id;locationUpdate(true);}renderDetail(c);}}
    $('#network').hidden=true;$('#connection').classList.remove('offline');$('#connection').title=data.worker_online?'连接正常':'执行器离线';$('#connection').setAttribute('aria-label',$('#connection').title);
    if(!data.worker_online&&current&&active(current.job)){$('#network').textContent='执行器暂时离线，排队记录已保存。';$('#network').hidden=false;}
  }catch(e){$('#network').textContent=e.message+' · 正在尝试重新连接';$('#network').hidden=false;$('#connection').classList.add('offline');$('#connection').setAttribute('aria-label','连接异常');}
  finally{busy=false;if(pending){pending=false;queueMicrotask(refresh);}}
}

function saveDraft(){session.set('draft',JSON.stringify({url:$('#video-url').value,language:$('#language').value,keep:$('#keep-video').checked}));session.set('form-key',formKey);}
function changed(){if(!submitting){formKey=crypto.randomUUID();saveDraft();}}
try{const d=JSON.parse(session.get('draft')||'{}');$('#video-url').value=d.url||'';$('#language').value=d.language||'zh';$('#keep-video').checked=!!d.keep;}catch{}
$('#video-url').oninput=changed;$('#language').onchange=$('#keep-video').onchange=changed;
$('#video-url').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#transcribe-form').requestSubmit();}};
function selectFile(f){if(submitting)return;file=f||null;$('#file-preview').hidden=!file;$('#video-url').hidden=!!file;$('#keep-video').disabled=!!file;$('#file-name').textContent=file?`${file.name} · ${(file.size/1024/1024).toFixed(1)} MB`:'';changed();}
$('#choose-file').onclick=()=>$('#upload-file').click();$('#upload-file').onchange=e=>selectFile(e.target.files[0]);$('#remove-file').onclick=()=>{selectFile(null);$('#upload-file').value='';$('#video-url').focus();};
for(const event of ['dragenter','dragover'])$('#transcribe-form').addEventListener(event,e=>{e.preventDefault();if(!submitting)$('#transcribe-form').classList.add('dragover');});
$('#transcribe-form').addEventListener('dragleave',()=>$('#transcribe-form').classList.remove('dragover'));
$('#transcribe-form').addEventListener('drop',e=>{e.preventDefault();$('#transcribe-form').classList.remove('dragover');if(e.dataTransfer.files.length)selectFile(e.dataTransfer.files[0]);});
function upload(body){return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/workbench/library/upload');xhr.timeout=900000;xhr.upload.onprogress=e=>{if(e.lengthComputable){const n=Math.round(e.loaded/e.total*100);$('#progress').value=n;$('#progress-label').textContent=n<100?`上传 ${n}%`:'上传完成，正在确认…';}};xhr.onerror=()=>reject(Error('上传连接中断，请再次提交确认'));xhr.ontimeout=()=>reject(Error('上传确认超时，请重试'));xhr.onload=()=>{try{const d=JSON.parse(xhr.responseText);if(xhr.status>=200&&xhr.status<300)resolve(d);else reject(Error(typeof d.detail==='string'?d.detail:'上传失败'));}catch{reject(Error('登录可能已过期，请刷新后重新登录'));}};xhr.send(body);});}
$('#transcribe-form').onsubmit=async e=>{
  e.preventDefault();if(submitting)return;$('#form-error').hidden=true;let body;
  if(file){if(!/\.(txt|mp3|mp4|wav|m4a|webm|mkv|ogg|flac|mov)$/i.test(file.name)||!file.size){error('请选择有效的音视频或 TXT 文件。','#form-error');return;}body=new FormData();body.set('file',file);body.set('language',$('#language').value);body.set('request_key',formKey);}
  else{let u;try{u=new URL($('#video-url').value.trim());}catch{}if(!u||!['http:','https:'].includes(u.protocol)){error('请粘贴完整的音视频链接。','#form-error');$('#video-url').focus();return;}}
  submitting=true;saveDraft();const controls=[...$('#transcribe-form').querySelectorAll('input,select,textarea,button')].map(n=>[n,n.disabled]);controls.forEach(([n])=>n.disabled=true);$('#submit').textContent=file?'上传中…':'提交中…';$('#upload-progress').hidden=!file;
  try{const d=file?await upload(body):await api('/library/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('#video-url').value.trim(),language:$('#language').value,keep_video:$('#keep-video').checked,request_key:formKey})});session.remove('draft');session.remove('form-key');formKey=crypto.randomUUID();$('#video-url').value='';$('#upload-file').value='';file=null;$('#file-preview').hidden=true;$('#video-url').hidden=false;submitting=false;open(d.content_id);if(d.reused)toast('这段音视频已转录过，已打开记录');}
  catch(err){error(err.message+'。输入已保留，可再次提交。','#form-error');}
  finally{submitting=false;controls.forEach(([n,disabled])=>n.disabled=disabled);if(!file)$('#keep-video').disabled=false;$('#submit').textContent='开始转录 ↗';$('#upload-progress').hidden=true;}
};
window.addEventListener('beforeunload',e=>{if(submitting){e.preventDefault();e.returnValue='';}});
$('#settings').onclick=()=>$('#settings-dialog').showModal();
for(const [id,attr,initial] of [['theme','theme','system'],['font-size','font','normal']]){let pref=initial;try{pref=localStorage.getItem('transcribe:'+id)||initial;}catch{}$('#'+id).value=pref;document.documentElement.dataset[attr]=pref;$('#'+id).onchange=e=>{document.documentElement.dataset[attr]=e.target.value;try{localStorage.setItem('transcribe:'+id,e.target.value);}catch{}};}
window.addEventListener('popstate',()=>{const p=new URLSearchParams(location.search);view=['archive','trash'].includes(p.get('view'))?p.get('view'):'library';$('#history-view').value=view;open(p.get('content')||p.get('task'),p.get('run')||'',false);});
const events=new EventSource('/api/workbench/events');events.onmessage=refresh;events.onopen=refresh;setInterval(refresh,5000);open(selected,run,false);
