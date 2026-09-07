import {renderMarkdown} from './markdown.js';
import {api} from './api.js';

const $ = s => document.querySelector(s);
const labels = {queued:'排队中',running:'处理中',completed:'已完成',failed:'需处理',interrupted:'已中断',cancelled:'已取消'};
const kinds = {script:'整理文稿',raw:'转录原文',summary:'摘要',translation:'翻译'};
const storage = {
  get(key, fallback='') {try{return sessionStorage.getItem('studio:'+key) || fallback;}catch{return fallback;}},
  set(key, value) {try{sessionStorage.setItem('studio:'+key,value);}catch{}},
  remove(key) {try{sessionStorage.removeItem('studio:'+key);}catch{}}
};
let params = new URLSearchParams(location.search);
let selected=params.get('content')||params.get('task'), run=params.get('run')||'', view=params.get('view')||'home';
if(!['home','library','archive','trash'].includes(view))view='home';
let query='', filter='', page=1, kind='script', current=null, busy=false, pending=false, actionBusy=false, submitting=false;
let detailSignature='', listSignature='', source='url', sse, formKey=storage.get('form-key')||crypto.randomUUID();
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
const date=stamp=>stamp?new Date(stamp).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'历史导入';
const active=job=>['queued','running'].includes(job.status);
function notify(message){$('#notice-text').textContent=message;$('#notice').hidden=false;}
$('#dismiss-notice').onclick=()=>$('#notice').hidden=true;
function button(text,handler,cls){const b=el('button',text,cls);b.type='button';b.onclick=handler;return b;}
function status(job){return el('span',labels[job.status]||job.status,'status '+job.status);}
function locationUpdate(replace=false){
  const u=new URL(location.href);u.search='';u.searchParams.set('view',view);
  if(selected)u.searchParams.set('content',selected);if(run)u.searchParams.set('run',run);
  history[replace?'replaceState':'pushState']({},'',u);
}
function openContent(id, selectedRun='', push=true){
  selected=id;run=selectedRun;current=null;kind='script';detailSignature='';listSignature='';
  $('#workspace').classList.toggle('has-selection',!!id);
  if(push)locationUpdate();
  if(!id){$('#reader').replaceChildren(el('div','从内容库选择一份内容，继续阅读；也可以新建内容。','empty'));document.body.classList.remove('focus-mode');}
  else $('#reader').replaceChildren(el('p','正在读取内容…','empty'));
  refresh();
}
function switchView(next){
  view=next;filter='';query='';page=1;$('#search').value='';updateNavigation();openContent(null);
}
function updateNavigation(){
  const titles={home:['YOUR WORKSPACE','继续你的阅读','正在处理、需要关注、最近完成的内容。'],library:['YOUR LIBRARY','内容库','让每一份内容，都能再次找到。'],archive:['ARCHIVE','已归档','暂时收起，不会删除文稿或原始文件。'],trash:['RECYCLE BIN','回收站','内容可随时恢复，不会自动永久删除。']};
  const t=titles[view];$('#view-kicker').textContent=t[0];$('#view-title').textContent=t[1];$('#view-description').textContent=t[2];
  document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-current',b.dataset.view===view?'page':'false'));
  document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.filter===filter)));
}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>switchView(b.dataset.view));
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;page=1;updateNavigation();refresh();});
let searchTimer;$('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{query=$('#search').value;page=1;refresh();},250);};
$('#previous').onclick=()=>{page--;refresh();};$('#next').onclick=()=>{page++;refresh();};
function renderList(data){
  $('#total').textContent=`${data.total} 份内容`;
  $('#page-label').textContent=`${page} / ${Math.max(1,Math.ceil(data.total/data.size))}`;
  $('#previous').disabled=page<=1;$('#next').disabled=page*data.size>=data.total;
  $('#queue-summary').textContent=data.worker_online?`${data.counts.running||0} 份处理中 · ${data.counts.queued||0} 份排队中`:'执行器离线 · 已提交内容仍保存在服务器';
  const signature=JSON.stringify(data.items)+selected;if(signature===listSignature)return;listSignature=signature;
  const fragment=document.createDocumentFragment();
  for(const content of data.items){
    const j=content.job,b=button('',()=>openContent(content.id),'content-item'+(content.id===selected?' selected':''));
    b.setAttribute('aria-current',content.id===selected?'true':'false');b.append(el('h3',content.title));
    const meta=el('div',null,'item-meta');meta.append(status(j),el('span',date(j.created_at)));
    if(content.attempt_count>1)meta.append(el('span',`${content.attempt_count} 次处理`));b.append(meta);
    if(active(j))b.append(el('div',j.status==='queued'?`队列第 ${j.queue_position} 位`:friendlyStage(j.stage),'item-stage'));
    fragment.append(b);
  }
  if(!data.items.length)fragment.append(el('p',query||filter?'没有匹配的内容，试试调整筛选。':view==='trash'?'回收站是空的。':view==='archive'?'还没有归档内容。':'从一个链接或一份文件开始。','empty'));
  $('#content-list').replaceChildren(fragment);
}
function friendlyStage(stage){
  if(/检测.*字幕/.test(stage))return '正在检查可用字幕';
  if(/下载/.test(stage))return '正在获取音视频内容';
  if(/优化|整理/.test(stage))return '正在整理转录文稿';
  if(/摘要/.test(stage))return '正在生成摘要';
  if(/翻译/.test(stage))return '正在翻译';
  if(/转录|Whisper/.test(stage))return '正在识别音频';
  return stage;
}
function errorTitle(job){
  const text=job.error||job.stage;
  if(/下载|SSL|HTTP Error|412|403/.test(text))return '内容获取失败';
  if(/摘要/.test(text))return '摘要生成未完成';
  if(/翻译/.test(text))return '翻译未完成';
  return job.status==='interrupted'?'本次处理已中断':'本次处理未完成';
}
function elapsed(){
  const n=$('#elapsed');if(!n||!current?.job.started_at)return;
  const j=current.job,seconds=Math.max(0,Math.floor(((j.finished_at?new Date(j.finished_at).getTime():Date.now())-new Date(j.started_at).getTime())/1000));
  n.textContent=`本次用时 ${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
}
async function mutate(content, action, b, title=''){
  if(actionBusy)return;
  if(action==='trash'&&!confirm('移入回收站？文稿和媒体不会被删除，可随时恢复。'))return;
  if(action==='cancel'&&!confirm('取消这次排队？已有成果会保留。'))return;
  actionBusy=true;b.disabled=true;const label=b.textContent;b.textContent='正在保存…';
  try{
    await api(`/library/${content.id}/action`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,title})});
    notify({trash:'已移入回收站，可随时恢复',restore:'已恢复到内容库',archive:'已归档，文稿仍保留',unarchive:'已移回内容库',rename:'标题已保存',cancel:'已取消排队，已有成果保留'}[action]);
    detailSignature='';listSignature='';
  }catch(e){notify(e.message);}finally{actionBusy=false;b.disabled=false;b.textContent=label;refresh();}
}
async function retryContent(content,b){
  if(actionBusy)return;
  if(content.job.status==='completed'&&!confirm('重新处理整份内容？旧结果会保留，本次会重新下载、转录并调用模型。'))return;
  actionBusy=true;b.disabled=true;b.textContent='正在提交…';notify('正在提交处理请求，请勿重复点击。');
  const keyName='retry:'+content.id,key=storage.get(keyName)||crypto.randomUUID();storage.set(keyName,key);
  try{
    const data=await api(`/library/${content.id}/retry`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_key:key})});
    storage.remove(keyName);run='';kind='script';locationUpdate(true);detailSignature='';
    notify(data.reused?'已找到这次处理记录，不会重复排队。':'已提交新一轮处理，旧文稿和处理记录均已保留。');
  }catch(e){notify(e.message+' 再次点击将使用相同请求标识确认，不会重复创建。');}
  finally{actionBusy=false;b.disabled=false;b.textContent='重试处理';refresh();}
}
async function copy(text){try{await navigator.clipboard.writeText(text);notify('已复制');}catch{notify('浏览器不允许复制，请选择文字或下载文件。');}}
function renderDetail(content){
  current=content;if(actionBusy)return;
  const signature=JSON.stringify(content)+kind;if(signature===detailSignature){elapsed();return;}detailSignature=signature;
  const root=$('#reader'),scroll=root.scrollTop,fragment=document.createDocumentFragment(),j=content.job;
  const head=el('div',null,'reader-head'),top=el('div',null,'reader-topline');
  top.append(button('返回内容列表',()=>openContent(null),'back-button'),status(j),button(document.body.classList.contains('focus-mode')?'退出专注':'专注阅读',()=>{document.body.classList.toggle('focus-mode');detailSignature='';renderDetail(current);}));head.append(top,el('h2',content.title));
  const meta=el('div',null,'content-meta');meta.append(el('span',`创建于 ${date(content.created_at)}`),el('span',`${content.attempt_count} 次处理`));
  if(j.started_at){const t=el('span');t.id='elapsed';meta.append(t);}if(content.archived)meta.append(el('span','已归档'));if(content.trashed)meta.append(el('span','已在回收站'));head.append(meta);
  if(/^https?:\/\//.test(j.source)){const link=el('a','查看原始来源','source-link');link.href=j.source;link.target='_blank';link.rel='noopener noreferrer';head.append(link);}else head.append(el('p',j.source,'source-link'));
  const actions=el('div',null,'reader-actions');
  actions.append(button('复制内容链接',()=>copy(location.href)));
  if(content.trashed)actions.append(button('恢复内容',e=>mutate(content,'restore',e.currentTarget),'primary'));
  else if(content.archived)actions.append(button('移回内容库',e=>mutate(content,'unarchive',e.currentTarget),'primary'));
  else{
    if(!active(j)&&!j.imported)actions.append(button(j.status==='completed'?'重新处理':'重试处理',e=>retryContent(content,e.currentTarget),'primary'));
    if(j.status==='queued')actions.append(button('取消排队',e=>mutate(content,'cancel',e.currentTarget)));
    actions.append(button('修改标题',e=>{const title=prompt('内容标题',content.title);if(title?.trim())mutate(content,'rename',e.currentTarget,title);}));
    if(!active(j))actions.append(button('归档',e=>mutate(content,'archive',e.currentTarget)),button('移入回收站',e=>mutate(content,'trash',e.currentTarget),'danger'));
  }head.append(actions);fragment.append(head);
  if(j.status!=='completed'){
    const failed=['failed','interrupted'].includes(j.status),box=el('div',null,'stage-box'+(failed?' error-state':''));
    const title=failed?errorTitle(j):j.status==='queued'?`已提交，排队第 ${j.queue_position} 位`:j.status==='cancelled'?'这次排队已取消':friendlyStage(j.stage);
    box.append(el('strong',title));
    const available=content.reading.available.length>0;
    box.append(el('p',failed?(available?'已经生成的文稿仍可阅读。重试将重新处理整份内容，不覆盖旧成果。':'尚无可读文稿。可以重试；若来源站限制下载，可改用文件上传。'):j.status==='cancelled'?'已有成果仍保留，可以重新处理。':'内容已保存在服务器。关闭网页不会取消后台处理。'));
    if(active(j)){const steps=el('ol',null,'steps');for(const name of ['获取内容','识别音频','整理文稿','生成摘要'])steps.append(el('li',name,friendlyStage(j.stage).includes(name.slice(0,2))?'current':''));box.append(steps);box.append(el('p',`最近更新 ${date(j.updated_at)} · 阶段用时取决于内容长度与模型，不显示估算百分比。`,'hint'));}
    if(j.error){const d=el('details');d.append(el('summary','技术详情'),el('p',j.error));box.append(d);}fragment.append(box);
  }
  const reading=content.reading,available=Object.keys(kinds).filter(k=>reading.result?.[k]);
  if(available.length){
    if(!available.includes(kind))kind=available[0];
    const area=el('div',null,'reading-area');
    if(reading.id!==j.id)area.append(el('p',`正在阅读第 ${content.attempts.length-content.attempts.findIndex(a=>a.id===reading.id)} 次处理的成果；最新一次为${labels[j.status]}。`,'provenance'));
    const tabs=el('nav',null,'result-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','文稿类型');
    function selectKind(key){kind=key;detailSignature='';renderDetail(current);$('#tab-'+key)?.focus();}
    for(const key of available){const b=button(kinds[key],()=>selectKind(key));b.id='tab-'+key;b.setAttribute('role','tab');b.setAttribute('aria-controls','result-panel');b.setAttribute('aria-selected',String(key===kind));b.tabIndex=key===kind?0:-1;b.onkeydown=e=>{let i=available.indexOf(key);if(e.key==='ArrowRight')i=(i+1)%available.length;else if(e.key==='ArrowLeft')i=(i+available.length-1)%available.length;else if(e.key==='Home')i=0;else if(e.key==='End')i=available.length-1;else return;e.preventDefault();selectKind(available[i]);};tabs.append(b);}area.append(tabs);
    const text=reading.result[kind],tools=el('div',null,'reading-tools');tools.append(el('span',`${text.length.toLocaleString()} 字符 · ${kinds[kind]}`));
    const links=el('div');links.append(button('复制文稿',()=>copy(text)));
    for(const format of ['md','txt']){const a=el('a',`下载 ${format.toUpperCase()}`);a.href=`/api/workbench/tasks/${reading.id}/export/${kind}?format=${format}`;a.setAttribute('download','');links.append(a);}tools.append(links);area.append(tools);
    area.append(el('p',kind==='raw'?'识别原文可能含错字或漏听，重要信息请结合原音视频核对。':'AI 生成内容可能重组或遗漏信息；可切换转录原文核对，不等同于逐字记录。','provenance'));
    const article=el('article',null,'reader-prose');article.id='result-panel';article.setAttribute('aria-label',kinds[kind]);article.append(renderMarkdown(text));
    const headings=[...article.querySelectorAll('h1,h2,h3')];if(headings.length>2){const toc=el('details',null,'toc');toc.append(el('summary',`文稿目录 · ${headings.length} 节`));const nav=el('nav');headings.forEach((h,i)=>{h.id='section-'+i;const a=el('a',h.textContent);a.href='#'+h.id;a.onclick=e=>{e.preventDefault();h.scrollIntoView({block:'start'});};nav.append(a);});toc.append(nav);area.append(toc);}area.append(article);fragment.append(area);
  }else if(j.status==='completed')fragment.append(el('p','这份内容没有可读取的文稿，请检查原始素材或迁移报告。','empty'));
  const history=el('details',null,'attempt-history');history.append(el('summary',`处理记录与成果版本 · ${content.attempt_count} 次`));
  content.attempts.forEach((attempt,index)=>{const row=el('div',null,'attempt');row.append(el('span',`第 ${content.attempt_count-index} 次 · ${date(attempt.created_at)}`),status(attempt));if(attempt.available.length)row.append(button(attempt.id===reading.id?'当前阅读版本':'阅读这次成果',()=>{run=attempt.id;locationUpdate();detailSignature='';refresh();}));history.append(row);});
  if(run)history.append(button('跟随最新成果',()=>{run='';locationUpdate();detailSignature='';refresh();}));
  const log=el('ol',null,'attempt-log');for(const event of j.events||[])log.append(el('li',`${date(event.at)} · ${friendlyStage(event.stage)}`));history.append(log);fragment.append(history);
  const expanded=root.querySelector('.attempt-history')?.open;root.replaceChildren(fragment);root.scrollTop=scroll;if(expanded)root.querySelector('.attempt-history').open=true;elapsed();
}
async function refresh(){
  if(busy){pending=true;return;}busy=true;
  const requestView=view,requestSelected=selected,requestRun=run;
  try{
    const data=await api(`/library?q=${encodeURIComponent(query)}&status=${filter}&view=${view}&page=${page}`);
    if(requestView!==view)return;
    if(page>1&&!data.items.length){page--;pending=true;return;}
    renderList(data);
    if(requestSelected){
      const content=await api(`/library/${encodeURIComponent(requestSelected)}${requestRun?'?run='+encodeURIComponent(requestRun):''}`);
      if(requestSelected===selected&&requestRun===run){if(content.id!==selected){selected=content.id;locationUpdate(true);}renderDetail(content);}
    }
    $('#connection').textContent=sse?.readyState===1?'实时同步中':'定时同步中';$('#network').hidden=true;
  }catch(e){$('#connection').textContent='连接异常';$('#network').textContent=e.message+' 已显示内容保留；连接恢复后自动同步。';$('#network').hidden=false;}
  finally{busy=false;if(pending){pending=false;queueMicrotask(refresh);}}
}
function saveDraft(){storage.set('draft',JSON.stringify({url:$('#video-url').value,language:$('#language').value,keep:$('#keep-video').checked}));storage.set('form-key',formKey);}
function formChanged(){if(!submitting){formKey=crypto.randomUUID();saveDraft();}}
for(const selector of ['#video-url','#language','#keep-video','#upload-file'])$(selector).addEventListener('change',formChanged);
$('#video-url').addEventListener('input',formChanged);
try{const draft=JSON.parse(storage.get('draft','{}'));$('#video-url').value=draft.url||'';$('#language').value=draft.language||'zh';$('#keep-video').checked=!!draft.keep;}catch{}
function showCreate(){$('#form-error').hidden=true;$('#create-dialog').showModal();$('#video-url').focus();}
$('#new-content').onclick=$('#welcome-new').onclick=showCreate;
$('#close-create').onclick=()=>{if(!submitting)$('#create-dialog').close();};
$('#create-dialog').addEventListener('cancel',e=>{if(submitting)e.preventDefault();});
document.querySelectorAll('[data-source]').forEach(b=>b.onclick=()=>{if(submitting)return;source=b.dataset.source;formChanged();document.querySelectorAll('[data-source]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));$('#url-field').hidden=source!=='url';$('#video-url').disabled=source!=='url';$('#upload-field').hidden=source!=='upload';$('#keep-video').disabled=source!=='url';});
for(const name of ['dragenter','dragover'])$('#dropzone').addEventListener(name,e=>{e.preventDefault();if(!submitting)$('#dropzone').classList.add('dragover');});
$('#dropzone').addEventListener('dragleave',()=>$('#dropzone').classList.remove('dragover'));
$('#dropzone').addEventListener('drop',e=>{e.preventDefault();$('#dropzone').classList.remove('dragover');if(!submitting&&e.dataTransfer.files.length){$('#upload-file').files=e.dataTransfer.files;formChanged();}});
function upload(body){return new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();xhr.open('POST','/api/workbench/library/upload');xhr.timeout=900000;
  xhr.upload.onprogress=e=>{if(e.lengthComputable){const n=Math.round(e.loaded/e.total*100);$('#progress').value=n;$('#progress-label').textContent=n<100?`已上传 ${n}% · 请勿关闭页面`:'上传完成，等待服务器确认…';}};
  xhr.onerror=()=>reject(Error('网络中断或登录已过期，尚未确认提交结果。请保留文件，再次提交可确认已有记录。'));
  xhr.ontimeout=()=>reject(Error('上传确认超时，请保留文件，再次提交将使用相同请求标识。'));
  xhr.onload=()=>{try{const d=JSON.parse(xhr.responseText);if(xhr.status>=200&&xhr.status<300)resolve(d);else reject(Error(typeof d.detail==='string'?d.detail:`上传失败 (${xhr.status})`));}catch{reject(Error('登录状态可能已过期，请刷新并重新登录；文件需要重新选择。'));}};
  xhr.send(body);
});}
$('#create-form').onsubmit=async e=>{
  e.preventDefault();if(submitting)return;
  $('#form-error').hidden=true;let body;
  try{if(source==='url'){if(!$('#video-url').value.trim())throw Error('请先输入内容链接');}else{const f=$('#upload-file').files[0];if(!f)throw Error('请先选择文件');if(!/\.(txt|mp3|mp4|wav|m4a|webm|mkv|ogg|flac|mov)$/i.test(f.name))throw Error('不支持的文件类型');if(f.size===0)throw Error('不能上传空文件');body=new FormData();body.set('file',f);body.set('language',$('#language').value);body.set('request_key',formKey);}}catch(err){$('#form-error').textContent=err.message;$('#form-error').hidden=false;return;}
  submitting=true;saveDraft();const locked=[...$('#create-form').querySelectorAll('input,select,button')].map(el=>[el,el.disabled]);locked.forEach(([el])=>el.disabled=true);$('#close-create').disabled=true;$('#submit').textContent=source==='url'?'正在提交…':'正在上传…';$('#upload-progress').hidden=source!=='upload';
  try{
    const result=source==='url'?await api('/library/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('#video-url').value.trim(),language:$('#language').value,keep_video:$('#keep-video').checked,request_key:formKey})}):await upload(body);
    $('#create-dialog').close();storage.remove('draft');storage.remove('form-key');formKey=crypto.randomUUID();$('#video-url').value='';$('#upload-file').value='';
    view='library';filter='';query='';page=1;$('#search').value='';updateNavigation();openContent(result.content_id);
    notify(result.reused?'这份内容已有记录，已为你打开；不会重复处理。归档或回收站内容可在详情中恢复。':'提交成功。内容已保存，后台会按顺序处理。');
  }catch(err){$('#form-error').textContent=err.message+' 输入已保留；再次提交会使用相同请求标识。';$('#form-error').hidden=false;}
  finally{submitting=false;locked.forEach(([el,disabled])=>el.disabled=disabled);$('#close-create').disabled=false;$('#submit').textContent='开始处理';}
};
window.addEventListener('beforeunload',e=>{if(submitting){e.preventDefault();e.returnValue='';}});
$('#settings').onclick=()=>$('#settings-dialog').showModal();
for(const [id,attr] of [['theme','theme'],['font-size','font']]){const select=$('#'+id);select.value=storage.get(id,id==='theme'?'system':'normal');document.documentElement.dataset[attr]=select.value;select.onchange=()=>{storage.set(id,select.value);document.documentElement.dataset[attr]=select.value;};}
window.addEventListener('popstate',()=>{params=new URLSearchParams(location.search);view=params.get('view')||'home';if(!['home','library','archive','trash'].includes(view))view='home';updateNavigation();openContent(params.get('content')||params.get('task'),params.get('run')||'',false);});
updateNavigation();$('#workspace').classList.toggle('has-selection',!!selected);
sse=new EventSource('/api/workbench/events');sse.onmessage=()=>refresh();sse.onopen=()=>refresh();sse.onerror=()=>{$('#connection').textContent='定时同步中';};
setInterval(refresh,5000);setInterval(elapsed,1000);refresh();
