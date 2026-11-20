// 仅在确有必要时添加标注，当前没有标注。
function annotationsFor() {
  return [];
}

// 用页面实际位置连接控件和页面边界外的编号说明。
export function updateAnnotations(route) {
  const sheet = document.querySelector('.prototype-sheet');
  const frame = document.querySelector('#app');
  const lines = document.querySelector('#annotation-lines');
  const notes = document.querySelector('#annotation-notes');
  lines.replaceChildren(); notes.replaceChildren();
  const sheetRect = sheet.getBoundingClientRect();
  const frameRect = frame.getBoundingClientRect();
  const canvas = document.querySelector('.prototype-canvas');
  const canvasRect = canvas.getBoundingClientRect();
  const visibleLeft = Math.max(frameRect.left, canvasRect.left);
  const visibleRight = Math.min(frameRect.right, canvasRect.right);
  const visibleCenter = visibleRight > visibleLeft ? (visibleLeft + visibleRight) / 2 : frameRect.left + frameRect.width / 2;
  // 画布滚动时，让弹框始终位于可见的页面区域。
  frame.style.setProperty('--dialog-shift-x', `${visibleCenter - (frameRect.left + frameRect.width / 2)}px`);
  frame.style.setProperty('--dialog-shift-y', `${canvas.scrollTop}px`);
  const frameHeight = frame.offsetHeight;
  const noteLeft = notes.offsetLeft;
  const targets = annotationsFor().map(([selector, text]) => {
    const element = frame.querySelector(selector);
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    const clip = element.closest('.assistant-scroll, .document-content')?.getBoundingClientRect() || frameRect;
    if (!rect.width || !rect.height || rect.bottom <= clip.top || rect.top >= clip.bottom) return null;
    return { rect, text };
  }).filter(Boolean).sort((a, b) => a.rect.top - b.rect.top);
  targets.forEach(({rect, text}, index) => {
    const x = rect.right - sheetRect.left;
    const y = Math.max(12, Math.min(frameHeight - 12, rect.top - sheetRect.top + rect.height / 2));
    const top = y - 11.5;
    const note = document.createElement('div');
    note.className = 'annotation-note'; note.style.top = `${top}px`;
    const number = document.createElement('span'); number.className = 'annotation-number'; number.textContent = index + 1;
    const description = document.createElement('span'); description.className = 'annotation-text'; description.textContent = text;
    note.append(number, description); notes.append(note);
    const svg = 'http://www.w3.org/2000/svg';
    const path = document.createElementNS(svg, 'path');
    const destination = top + 11.5;
    path.setAttribute('d', `M ${x} ${y} L ${noteLeft} ${destination}`);
    const dot = document.createElementNS(svg, 'circle');
    dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('r', '3');
    lines.append(path, dot);
  });
}
