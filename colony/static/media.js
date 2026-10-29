// A viewport viewer, including on iPhone where element Fullscreen is unavailable.
if (!window.colonyMedia) {
  window.colonyMedia = true;
  const original = window.colonyNav && colonyNav.keep;
  const listen = (target, type, fn, options) => (original ? original.add : EventTarget.prototype.addEventListener).call(target, type, fn, options);
  const later = (fn) => (original ? original.st : window.setTimeout).call(window, fn, 100);
  let library;
  const pdfLibrary = () => library ||= import('/static/pdfjs/6.4.299/pdf.mjs').then((pdf) => {
    pdf.GlobalWorkerOptions.workerSrc = '/static/pdfjs/6.4.299/pdf.worker.mjs'; return pdf;
  });
  const trusted = (source) => {
    const u = new URL(source, location.href);
    if (u.origin !== location.origin || u.pathname !== '/open' || !u.searchParams.has('f') || !u.searchParams.has('p') || u.searchParams.has('u')) throw Error('File unavailable');
    return u.href;
  };
  const documentTask = async (source) => {
    const pdf = await pdfLibrary(), base = '/static/pdfjs/6.4.299/';
    return pdf.getDocument({url: trusted(source), withCredentials: true, enableXfa: false,
      disableAutoFetch: true, disableStream: true, cMapUrl: base + 'cmaps/', cMapPacked: true,
      standardFontDataUrl: base + 'standard_fonts/', wasmUrl: base + 'wasm/', iccUrl: base + 'iccs/'});
  };
  const canvasFor = async (page, width, maximum = 2_000_000) => {
    const unit = page.getViewport({scale: 1}), scale = Math.min(width / unit.width * Math.min(devicePixelRatio || 1, 2), Math.sqrt(maximum / (unit.width * unit.height)));
    const viewport = page.getViewport({scale}), canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.floor(viewport.width)); canvas.height = Math.max(1, Math.floor(viewport.height));
    const task = page.render({canvasContext: canvas.getContext('2d', {alpha: false}), viewport});
    return {canvas, task};
  };

  // Previews are serial, with at most four retained canvases and no retained PDF documents/workers.
  let previews = Promise.resolve();
  const retained = [], observed = new Set(), previewState = new WeakMap();
  let dialog;
  const observer = new IntersectionObserver((entries) => entries.forEach((entry) => {
    if (!entry.isIntersecting && previewState.get(entry.target) === 'evicted') previewState.delete(entry.target);
    if (!entry.isIntersecting || previewState.has(entry.target)) return;
    const target = entry.target; previewState.set(target, 'queued'); observer.unobserve(target); observed.delete(target);
    previews = previews.then(async () => {
      if (!target.isConnected) { previewState.delete(target); return; }
      let task;
      try {
        task = await documentTask(target.closest('[data-media]').dataset.src);
        const doc = await task.promise, page = await doc.getPage(1);
        const rendered = await canvasFor(page, Math.max(200, target.clientWidth), 1_000_000);
        await rendered.task.promise;
        if (!target.isConnected) { rendered.canvas.width = rendered.canvas.height = 0; return; }
        const unit = page.getViewport({scale: 1}); target.style.aspectRatio = `${unit.width} / ${unit.height}`;
        target.replaceChildren(rendered.canvas); target.setAttribute('aria-label', `PDF preview, ${doc.numPages} pages`);
        retained.push(target);
        while (retained.length > 4) { const old = retained.shift(), canvas = old.querySelector('canvas');
          if (canvas) canvas.width = canvas.height = 0;
          old.textContent = 'PDF preview'; previewState.set(old, 'evicted');
          if (old.isConnected) { observed.add(old); observer.observe(old); } }
      } catch (error) { target.textContent = 'Open PDF to view'; }
      finally { if (task) await task.destroy(); }
    }).catch(() => {});
  }), {rootMargin: '200px'});
  const scan = () => {
    if (dialog && !dialog.isConnected) document.body.append(dialog);
    for (const el of observed) if (!el.isConnected) { observer.unobserve(el); observed.delete(el); }
    document.querySelectorAll('.media-pdf-preview').forEach((el) => { if (!previewState.has(el)) { observed.add(el); observer.observe(el); } });
  };
  new MutationObserver(scan).observe(document.body, {childList: true, subtree: true}); scan();

  dialog = document.createElement('dialog'); dialog.className = 'media-viewer';
  dialog.setAttribute('aria-label', 'Media viewer');
  dialog.innerHTML = `<div class="media-tools"><span class="media-title"></span><a class="media-download">Download</a><button type="button" class="media-close" aria-label="Close viewer">✕</button>
    <div class="media-zooms" aria-label="Zoom">${[1, 1.5, 2, 2.5, 3].map((n) => `<button type="button" data-zoom="${n}" aria-pressed="${n === 1}">${n === 1 ? 'Fit' : n + '×'}</button>`).join('')}</div></div>
    <div class="media-stage" tabindex="0" aria-label="Media, scroll to explore"><div class="media-content"></div></div>`;
  document.body.append(dialog);
  const stage = dialog.querySelector('.media-stage'), content = dialog.querySelector('.media-content'), title = dialog.querySelector('.media-title');
  const download = dialog.querySelector('.media-download');
  let active = null, generation = 0, pdfTask = null, pdf = null, pages = [], rendered = new Map(), rendering = null, queue = [], zoom = 1;
  let source = null, bodyStyle = null, scroll = null, inerted = [], position = {x: .5, y: 0}, sizing = false, renderVersion = 0;
  const status = (text) => { const p = document.createElement('p'); p.className = 'media-status'; p.textContent = text; content.replaceChildren(p); };
  const clearCanvases = () => { for (const node of rendered.values()) { node.width = node.height = 0; node.remove(); } rendered.clear(); };
  const cleanupPdf = () => {
    renderVersion += 1; queue = []; if (rendering) rendering.cancel(); rendering = null;
    clearCanvases(); pdf = null; pages = [];
    if (pdfTask) { const old = pdfTask; pdfTask = null; old.destroy().catch(() => {}); }
  };
  const remember = () => { if (sizing || !active) return;
    position = {x: (stage.scrollLeft + stage.clientWidth / 2) / Math.max(1, content.offsetWidth),
      y: stage.scrollTop / Math.max(1, content.offsetHeight)};
  };
  const visiblePages = () => pages.map((node, index) => ({node, index, top: node.offsetTop - stage.scrollTop}))
    .filter((x) => x.top < stage.clientHeight + 150 && x.top + x.node.offsetHeight > -150)
    .sort((a, b) => Math.abs(a.top) - Math.abs(b.top)).slice(0, 3);
  const drawPages = async () => {
    if (!pdf || !active) return;
    const visible = visiblePages(), keep = new Set(visible.map((x) => x.index));
    for (const [i, canvas] of rendered) if (!keep.has(i)) { canvas.width = canvas.height = 0; canvas.remove(); rendered.delete(i); }
    queue = visible.map((x) => x.index).filter((i) => !rendered.has(i));
    if (rendering || !queue.length) return;
    const mine = generation, version = renderVersion, doc = pdf, i = queue.shift();
    // Reserve the slot before getPage as scroll/resize callbacks may run during it.
    const reserved = {cancel() {}}; rendering = reserved;
    try {
      const page = await doc.getPage(i + 1);
      if (mine !== generation || version !== renderVersion || doc !== pdf) return;
      const unit = page.getViewport({scale: 1}); pages[i].style.aspectRatio = `${unit.width} / ${unit.height}`;
      const result = await canvasFor(page, content.offsetWidth); rendering = result.task;
      await result.task.promise;
      if (mine !== generation || version !== renderVersion || doc !== pdf || !visiblePages().some((x) => x.index === i)) { result.canvas.width = result.canvas.height = 0; return; }
      pages[i].replaceChildren(result.canvas); rendered.set(i, result.canvas);
    } catch (error) { if (error.name !== 'RenderingCancelledException' && mine === generation) title.textContent = 'PDF page could not be shown'; }
    finally { if (mine === generation && version === renderVersion) { rendering = null; if (queue.length) drawPages(); } }
  };
  const layout = () => {
    if (!active || !dialog.open) return;
    sizing = true; const width = stage.clientWidth * zoom; content.style.width = `${width}px`;
    const chart = content.querySelector('.media-chart');
    if (chart) { const table = chart.querySelector('table'); chart.style.transform = 'none';
      const scale = width / table.getBoundingClientRect().width; content.style.height = `${table.offsetHeight * scale}px`; chart.style.transform = `scale(${scale})`; }
    stage.scrollLeft = position.x * width - stage.clientWidth / 2;
    stage.scrollTop = position.y * content.offsetHeight;
    requestAnimationFrame(() => { sizing = false; remember(); if (pdf) drawPages(); });
  };
  const resized = () => { if (!active) return;
    if (pdf) { renderVersion += 1; if (rendering) rendering.cancel(); rendering = null; clearCanvases(); }
    layout();
  };
  new ResizeObserver(resized).observe(stage);
  listen(window, 'orientationchange', () => later(resized));
  if (window.visualViewport) listen(visualViewport, 'resize', () => later(resized));
  stage.addEventListener('scroll', () => { remember(); if (pdf) drawPages(); }, {passive: true});
  const close = () => {
    if (!active) return;
    generation += 1; cleanupPdf(); active = null;
    if (dialog.open) { if (dialog.close) dialog.close(); else dialog.removeAttribute('open'); }
    document.documentElement.classList.remove('media-open');
    for (const [key, value] of Object.entries(bodyStyle || {})) document.body.style[key] = value;
    for (const [node, value] of inerted) node.inert = value; inerted = [];
    content.replaceChildren(); content.style.cssText = '';
    if (scroll) {
      window.scrollTo(scroll.x, scroll.y);
      if (scroll.anchor.isConnected) window.scrollBy(0, scroll.anchor.getBoundingClientRect().top - scroll.top);
    }
    if (source && source.isConnected) source.focus({preventScroll: true});
    bodyStyle = scroll = source = null;
  };
  dialog.addEventListener('cancel', (ev) => { ev.preventDefault(); close(); });
  dialog.querySelector('.media-close').addEventListener('click', close);
  dialog.querySelector('.media-zooms').addEventListener('click', (ev) => {
    const button = ev.target.closest('[data-zoom]'); if (!button) return;
    remember(); zoom = +button.dataset.zoom;
    dialog.querySelectorAll('[data-zoom]').forEach((b) => b.setAttribute('aria-pressed', String(b === button))); resized();
  });
  // Native dialog supplies a focus trap and background inertness. Escape also works in the CSS fallback.
  listen(document, 'keydown', (ev) => {
    if (!active) return;
    if (ev.key === 'Escape') { ev.preventDefault(); close(); }
    if (ev.key === 'Tab') { const choices = [...dialog.querySelectorAll('button, a[href], [tabindex]')].filter((el) => el.getClientRects().length && !el.disabled);
      const first = choices[0], last = choices[choices.length - 1];
      if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    }
  });
  listen(document, 'click', async (ev) => {
    const button = ev.target.closest('.media-expand'); if (!button) return;
    ev.preventDefault(); ev.stopPropagation();
    const media = button.closest('[data-media]'); if (!media) return;
    if (active) close(); source = button; active = media.dataset.media; const mine = ++generation;
    zoom = 1; position = {x: .5, y: 0}; title.textContent = media.dataset.label || 'Chart';
    dialog.querySelectorAll('[data-zoom]').forEach((b) => b.setAttribute('aria-pressed', String(+b.dataset.zoom === 1)));
    content.style.cssText = ''; content.replaceChildren();
    download.hidden = active === 'table';
    if (!download.hidden) { const u = new URL(trusted(media.dataset.src)); u.searchParams.set('download', '1'); download.href = u.href; download.download = ''; }
    scroll = {x: scrollX, y: scrollY, anchor: media, top: media.getBoundingClientRect().top}; bodyStyle = {};
    for (const key of ['position', 'top', 'left', 'right', 'width', 'overflow']) bodyStyle[key] = document.body.style[key];
    Object.assign(document.body.style, {position: 'fixed', top: `-${scroll.y}px`, left: `-${scroll.x}px`, right: '0', width: '100%', overflow: 'hidden'});
    document.documentElement.classList.add('media-open');
    if (dialog.showModal) dialog.showModal(); else {
      dialog.setAttribute('open', ''); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true');
      inerted = [...document.body.children].filter((node) => node !== dialog).map((node) => [node, node.inert]);
      inerted.forEach(([node]) => node.inert = true);
    }
    dialog.querySelector('.media-close').focus({preventScroll: true});
    if (active === 'image') {
      const image = document.createElement('img'); image.alt = title.textContent; image.src = trusted(media.dataset.src);
      image.addEventListener('load', layout); content.append(image); layout();
    } else if (active === 'table') {
      const chart = document.createElement('div'); chart.className = 'media-chart'; const table = media.querySelector('table').cloneNode(true);
      table.querySelectorAll('[id]').forEach((node) => node.removeAttribute('id')); chart.append(table); content.append(chart); layout();
    } else {
      status('Loading PDF…');
      try {
        const task = await documentTask(media.dataset.src);
        if (mine !== generation) { task.destroy(); return; } pdfTask = task;
        const doc = await task.promise;
        if (mine !== generation) return; pdf = doc;
        const first = await doc.getPage(1), unit = first.getViewport({scale: 1});
        if (mine !== generation) return;
        content.replaceChildren(); pages = Array.from({length: doc.numPages}, (_, i) => { const page = document.createElement('div');
          page.className = 'media-pdf-page'; page.style.aspectRatio = `${unit.width} / ${unit.height}`; page.setAttribute('aria-label', `Page ${i + 1}`); content.append(page); return page; });
        title.textContent = `${media.dataset.label} · ${doc.numPages} pages`; layout(); drawPages();
      } catch (error) { if (mine === generation) status('PDF could not be shown. Download to open it.'); }
    }
  }, true);
}
