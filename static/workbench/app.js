import {renderMarkdown} from './markdown.js';
import {api} from './api.js';
const $ = s => document.querySelector(s);
const labels = {queued:'排队中',running:'执行中',completed:'已完成',failed:'失败',interrupted:'已中断'};
const kinds = {script:'整理文稿',raw:'原始转录',summary:'摘要',translation:'翻译'};
let selected=new URLSearchParams(location.search).get('task'), page=1, filter='', query='', current=null, activeKind='script', busy=false, lastRender='', listRender='', source='url', formKey=crypto.randomUUID(), sse;
const el = (tag, text, cls) => {const n=document.createElement(tag); if(text!=null)n.textContent=text; if(cls)n.className=cls; return n;};
const date = stamp => stamp ? new Date(stamp).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '未开始';
function notify(text) { $('#notice').textContent=text; $('#notice').hidden=false; }
function updateElapsed(){
  const node=$('#elapsed');
  if(!node||!current?.started_at)return;
  const seconds=Math.max(0,Math.floor(((current.finished_at?new Date(current.finished_at).getTime():Date.now())-new Date(current.started_at).getTime())/1000));
  node.textContent=`执行耗时 ${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
}
function openTask(id, push=true) {
  selected=id; current=null; lastRender=''; activeKind='script';
  if(push){const u=new URL(location.href); if(id)u.searchParams.set('task',id);else u.searchParams.delete('task');history.pushState({},'',u);}
  $('.workspace').classList.toggle('has-selection',!!id);
  if(!id)$('#detail').replaceChildren(el('p','从左侧选择任务，查看进度或阅读结果。','empty'));
  refresh();
}
function renderList(data) {
  $('#total').textContent=data.total;
  $('#queue-summary').textContent=data.worker_online?`${data.counts.running||0} 个执行中 · ${data.counts.queued||0} 个排队中`:'执行器离线，排队任务已保存';
  $('#page-label').textContent=`第 ${page} / ${Math.max(1,Math.ceil(data.total/data.size))} 页`;
  $('#previous').disabled=page===1; $('#next').disabled=page*data.size>=data.total;
  const signature=JSON.stringify(data.items)+selected;
  if(signature===listRender)return;
  listRender=signature;
  const fragment=document.createDocumentFragment();
  for(const task of data.items){
    const button=el('button',null,'task'+(task.id===selected?' selected':''));button.dataset.id=task.id;button.setAttribute('aria-current',task.id===selected?'true':'false');
    button.append(el('h3',task.title));
    const meta=el('div',null,'task-meta');meta.append(el('span',labels[task.status],`status ${task.status}`),el('span',task.imported?'历史导入':date(task.created_at)));button.append(meta);
    if(['running','queued'].includes(task.status))button.append(el('div',task.status==='queued'?`队列第 ${task.queue_position} 位`:task.stage,'task-stage'));
    button.onclick=()=>openTask(task.id);fragment.append(button);
  }
  if(!data.items.length)fragment.append(el('p',query||filter?'没有匹配的任务，试试调整筛选条件。':'还没有任务。提交链接或上传文件即可开始。','empty'));
  $('#task-list').replaceChildren(fragment);
}
function renderDetail(task) {
  current=task;
  const signature=JSON.stringify(task)+activeKind;
  if(signature===lastRender){updateElapsed();return;}
  lastRender=signature;
  const root=$('#detail'), frag=document.createDocumentFragment();
  const back=el('button','返回任务列表','back');back.onclick=()=>openTask(null);frag.append(back);
  const header=el('div',null,'detail-header');header.append(el('span',labels[task.status],`status ${task.status}`),el('h2',task.title));
  const meta=el('div',null,'detail-meta');meta.append(el('div',task.imported?'旧版本历史记录，原始创建时间未知':`提交 ${date(task.created_at)}${task.finished_at?' · 结束 '+date(task.finished_at):''}`));
  const sourceNode=el('div',task.source);meta.append(sourceNode);
  if(task.started_at){const elapsed=el('div');elapsed.id='elapsed';meta.append(elapsed);}
  if(task.retry_of)meta.append(el('div','重试任务，原记录保留'));
  header.append(meta);frag.append(header);
  const actions=el('div',null,'detail-actions');
  const link=el('button','复制任务链接');link.onclick=()=>copy(location.href,'任务链接已复制，打开时仍需登录');actions.append(link);
  if(task.can_retry){const retry=el('button','重新排队');retry.onclick=async()=>{retry.disabled=true;try{const d=await api(`/tasks/${task.id}/retry?request_key=${crypto.randomUUID()}`,{method:'POST'});openTask(d.id);}catch(e){notify(e.message);retry.disabled=false;}};actions.append(retry);}
  frag.append(actions);
  if(task.status!=='completed'){
    const panel=el('div',null,'stage-panel');panel.append(el('strong',task.status==='queued'?`排队第 ${task.queue_position} 位`:task.stage));
    panel.append(el('p',task.error|| (task.status==='queued'?'前面的任务完成后会自动开始。你可以关闭页面。':'后台正在处理，页面会自动更新。阶段耗时取决于视频长度和模型响应。'),task.error?'error':''));
    if(task.started_at)panel.append(el('p',`开始于 ${date(task.started_at)}`));frag.append(panel);
  }
  const available=Object.keys(kinds).filter(k=>task.result?.[k]);
  if(available.length){
    if(!available.includes(activeKind))activeKind=available[0];
    const tabs=el('nav',null,'tabs');tabs.setAttribute('aria-label','结果类型');tabs.setAttribute('role','tablist');
    for(const k of available){const b=el('button',kinds[k]);b.setAttribute('role','tab');b.setAttribute('aria-selected',String(k===activeKind));b.onclick=()=>{activeKind=k;lastRender='';renderDetail(current);};tabs.append(b);}frag.append(tabs);
    const toolbar=el('div',null,'reader-toolbar');const text=task.result[activeKind];
    toolbar.append(el('span',`${text.length.toLocaleString()} 字符${activeKind==='script'?' · AI 整理，非逐字原文':''}`));
    const downloads=el('div');const cp=el('button','复制文案');cp.onclick=()=>copy(text,'文案已复制');downloads.append(cp);
    for(const fmt of ['md','txt']){const a=el('a',`下载 ${fmt.toUpperCase()}`);a.href=`/api/workbench/tasks/${task.id}/export/${activeKind}?format=${fmt}`;a.setAttribute('download','');downloads.append(a);}toolbar.append(downloads);frag.append(toolbar);
    const reader=el('article',null,'reader');reader.setAttribute('aria-label',kinds[activeKind]);reader.append(renderMarkdown(text));frag.append(reader);
  }else if(task.status==='completed'){frag.append(el('p','这个历史任务没有可读取的文案。请检查迁移报告中的缺失文件。','empty'));}
  if(task.events?.length){const timeline=el('details',null,'timeline');timeline.append(el('summary','处理记录'));const ol=el('ol');for(const e of task.events)ol.append(el('li',`${date(e.at)}  ${e.stage}`));timeline.append(ol);frag.append(timeline);}
  const scroll=root.scrollTop; root.replaceChildren(frag);root.scrollTop=scroll;updateElapsed();
}
async function refresh(){
  if(busy)return;busy=true;
  try{
    const data=await api(`/tasks?q=${encodeURIComponent(query)}&status=${filter}&page=${page}`);renderList(data);
    if(selected){try{const task=await api('/tasks/'+encodeURIComponent(selected));if(task.id===selected)renderDetail(task);}catch(e){$('#detail').replaceChildren(el('p',e.message,'empty error'));}}
    $('#connection').textContent=sse?.readyState===1?'实时同步中':'定时同步中';
  }catch(e){$('#connection').textContent='连接异常，自动重试';notify(e.message);}finally{busy=false;}
}
async function copy(text,message){try{await navigator.clipboard.writeText(text);notify(message);}catch{notify('浏览器不允许复制，请手动选择文字或下载文件。');}}
function showDialog(){formKey=crypto.randomUUID();$('#form-error').hidden=true;$('#create-dialog').showModal();}
$('#new-task').onclick=showDialog;$('#empty-new').onclick=showDialog;
$('#close-dialog').onclick=$('#cancel-dialog').onclick=()=>$('#create-dialog').close();
document.querySelectorAll('[data-source]').forEach(b=>b.onclick=()=>{source=b.dataset.source;document.querySelectorAll('[data-source]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));$('#url-field').hidden=source!=='url';$('#video-url').disabled=source!=='url';$('#keep-video').disabled=source!=='url';$('#upload-field').hidden=source!=='upload';});
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;page=1;document.querySelectorAll('[data-filter]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));refresh();});
let searchTimer;$('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{query=$('#search').value;page=1;refresh();},250);};
$('#previous').onclick=()=>{page--;refresh();};$('#next').onclick=()=>{page++;refresh();};
$('#create-form').onsubmit=async event=>{
  event.preventDefault();$('#submit').disabled=true;$('#submit').textContent='正在提交…';$('#form-error').hidden=true;
  try{
    let result;
    if(source==='url'){const url=$('#video-url').value.trim();if(!url)throw Error('请先填写视频链接');result=await api('/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,language:$('#language').value,keep_video:$('#keep-video').checked,request_key:formKey})});}
    else{const file=$('#upload-file').files[0];if(!file)throw Error('请先选择文件');const body=new FormData();body.set('file',file);body.set('language',$('#language').value);body.set('request_key',formKey);result=await api('/uploads',{method:'POST',body});}
    $('#create-dialog').close();$('#create-form').reset();page=1;filter='';document.querySelectorAll('[data-filter]').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.filter==='')));query='';$('#search').value='';openTask(result.id);notify('任务已加入队列，关闭页面也会继续执行。');
  }catch(e){$('#form-error').textContent=e.message;$('#form-error').hidden=false;}finally{$('#submit').disabled=false;$('#submit').textContent='加入队列';}
};
window.addEventListener('popstate',()=>openTask(new URLSearchParams(location.search).get('task'),false));
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
sse=new EventSource('/api/workbench/events');sse.onmessage=()=>refresh();sse.onerror=()=>{$('#connection').textContent='实时连接恢复中，定时查询兜底';};
setInterval(()=>{if(!document.hidden)refresh();},5000);
$('.workspace').classList.toggle('has-selection',!!selected);refresh();
