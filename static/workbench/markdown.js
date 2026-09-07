// Deliberately small safe Markdown reader: never executes HTML, links or images.
// Unknown Markdown is shown literally instead of passing it through innerHTML.
function inline(node, text) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  for (const part of parts) {
    if (part.startsWith('**') && part.endsWith('**')) {
      const strong = document.createElement('strong'); strong.textContent = part.slice(2,-2); node.append(strong);
    } else if (part.startsWith('`') && part.endsWith('`')) {
      const code = document.createElement('code'); code.textContent = part.slice(1,-1); node.append(code);
    } else node.append(document.createTextNode(part));
  }
}
export function renderMarkdown(text) {
  const fragment = document.createDocumentFragment();
  let paragraph = [], code = null, list = null;
  function flush() { if (paragraph.length) { const p = document.createElement('p'); inline(p,paragraph.join('\n')); fragment.append(p); paragraph=[]; } list=null; }
  for (const line of text.split('\n')) {
    if (line.startsWith('```')) { flush(); if (code) { fragment.append(code); code=null; } else code=document.createElement('pre'); continue; }
    if (code) { code.textContent += line+'\n'; continue; }
    const heading = line.match(/^(#{1,6})\s+(.+)/);
    const item = line.match(/^\s*(?:[-*+]\s+|\d+\.\s+)(.+)/);
    if (heading) { flush(); const h=document.createElement('h'+heading[1].length); inline(h,heading[2]); fragment.append(h); }
    else if (item) { if (!list) { flush(); list=document.createElement('ul'); fragment.append(list); } const li=document.createElement('li'); inline(li,item[1]); list.append(li); }
    else if (line.startsWith('> ')) { flush(); const q=document.createElement('blockquote'); inline(q,line.slice(2)); fragment.append(q); }
    else if (!line.trim()) flush();
    else paragraph.push(line);
  }
  flush(); if(code)fragment.append(code); return fragment;
}
