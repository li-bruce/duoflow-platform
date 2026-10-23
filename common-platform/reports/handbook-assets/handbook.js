/* Static handbook controls. No backend calls, credentials, or business mutations. */
(() => {
  'use strict';
  const article = document.getElementById('doc-content');
  const tools = document.querySelector('.tools');
  if (!article || !tools) return;
  const original = Array.from(article.childNodes);
  let section = document.createElement('section');
  section.className = 'doc-introduction';
  article.append(section);
  for (const node of original) {
    if (node.nodeType === 1 && node.tagName === 'H2') {
      section = document.createElement('details');
      section.className = 'doc-section';
      section.open = true;
      const summary = document.createElement('summary');
      summary.append(node);
      section.append(summary);
      article.append(section);
    } else {
      section.append(node);
    }
  }
  const sections = Array.from(article.children);
  const chapters = sections.filter(node => node.matches('.doc-section'));
  const search = document.getElementById('doc-search');
  const status = document.getElementById('search-status');
  const tocLinks = Array.from(document.querySelectorAll('.toc a'));
  const normalized = text => text.toLocaleLowerCase().replace(/\s+/g, ' ').trim();
  function filter() {
    const query = normalized(search.value);
    let matches = 0;
    sections.forEach(node => {
      const match = !query || normalized(node.textContent).includes(query);
      node.hidden = !match;
      if (match && query && node.tagName === 'DETAILS') node.open = true;
      if (match && node.textContent.trim()) matches++;
    });
    tocLinks.forEach(link => {
      const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
      link.hidden = !!(target && target.closest('[hidden]'));
    });
    status.textContent = query ? `找到 ${matches} 个内容区段` : `${chapters.length} 个章节 · 点击标题可折叠`;
  }
  function revealHash() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    const target = id && document.getElementById(id);
    if (!target || !article.contains(target)) return;
    if (search.value) { search.value = ''; filter(); }
    const parent = target.closest('.doc-section');
    if (parent) { parent.hidden = false; parent.open = true; }
    if (typeof target.scrollIntoView === 'function') target.scrollIntoView({block: 'start'});
  }
  search.addEventListener('input', filter);
  document.getElementById('clear-search').addEventListener('click', () => {search.value = ''; filter(); search.focus();});
  document.getElementById('expand-all').addEventListener('click', () => chapters.forEach(node => {node.open = true;}));
  document.getElementById('collapse-all').addEventListener('click', () => chapters.forEach(node => {node.open = false;}));
  tocLinks.forEach(link => link.addEventListener('click', () => {
    const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
    const parent = target && target.closest('.doc-section');
    if (parent) parent.open = true;
  }));
  window.addEventListener('hashchange', revealHash);
  tools.hidden = false;
  filter();
  revealHash();
})();
