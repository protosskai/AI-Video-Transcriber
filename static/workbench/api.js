// All requests use same-origin credentials; no API keys in browser storage.
export async function api(path, options={}) {
  const timeout = options.body instanceof FormData ? 900000 : 20000;
  let response;
  try { response = await fetch('/api/workbench'+path, {...options, signal:AbortSignal.timeout(timeout)}); }
  catch { throw Error('网络请求未能确认，请检查连接或刷新重新登录'); }
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try { const body=await response.json(); if(typeof body.detail==='string')message=body.detail; } catch {}
    throw Error(message);
  }
  if (!(response.headers.get('content-type')||'').includes('application/json')) {
    throw Error('登录状态可能已过期，请刷新页面重新登录');
  }
  return response.json();
}
