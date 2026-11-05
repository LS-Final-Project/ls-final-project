const NUMBERING = /^\s*(?:\d+(?:\.\d+)+\.?|\d+[.)]|\(\d+\)|[\u2460-\u2473]|[가-하][.)])\s*$/u;
const TOC_ROW = /(?:\.{4,}|…{2,}|·{4,})\s*\d+\s*$/;

/** Match only a unique literal quote, allowing layout whitespace but no fuzzy/value changes. */
export function highlightQuote(root: HTMLElement, quote: string): number {
  for (const mark of root.querySelectorAll('mark[data-rfp-quote]')) mark.replaceWith(...mark.childNodes);
  root.normalize();
  const needle = quote.replace(/\s+/g, ' ').trim();
  if (!needle) return 0;
  const walker = root.ownerDocument.createTreeWalker(root, 5);
  const positions: { node: Text; offset: number }[] = [];
  const furniture = runningPageText(root);
  let text = '', previousBlock: Element | null = null, lineBreak = false, lineStart = true, afterNumber = false;
  let node: Node | null;
  while ((node = walker.nextNode())) {
    if (node.nodeType === 1) {
      if ((node as Element).tagName === 'BR' && !(node as Element).closest('del,[aria-hidden="true"]')) lineBreak = true;
      continue;
    }
    const parent = node.parentElement;
    if (!parent || parent.closest('style,script,del,[aria-hidden="true"]')) continue;
    const pagePart = parent.closest('section.docx > article > *');
    if (pagePart && furniture.has(pagePart)) { lineBreak = true; continue; }
    const block = parent.closest('p,td,li');
    // A following run that starts with a digit may continue a value (1. + 5 kV = 1.5 kV); never split it.
    const numberEnds = afterNumber && !/^[\p{N}.,/]/u.test(node.textContent?.trimStart() ?? '');
    if ((lineBreak || numberEnds || previousBlock && block !== previousBlock) && text && !text.endsWith(' ')) {
      text += ' '; positions.push({ node: node as Text, offset: 0 });
    }
    if (lineBreak || block !== previousBlock) lineStart = true;
    previousBlock = block; lineBreak = false;
    if (node.textContent?.trim()) {
      // DOCX list numbers (6.2.1, 1), ②, 가.) render as their own run glued to the body text; keep them a separate word.
      afterNumber = lineStart && NUMBERING.test(node.textContent);
      lineStart = false;
    }
    for (let offset = 0; offset < (node.textContent?.length ?? 0); offset++) {
      const character = node.textContent![offset];
      if (/\s/.test(character)) {
        if (!text || text.endsWith(' ')) continue;
        text += ' ';
      } else text += character;
      positions.push({ node: node as Text, offset });
    }
  }
  const word = (value: string) => /[\p{L}\p{N}_]/u.test(value);
  const matches: number[] = [];
  for (let at = text.indexOf(needle); at >= 0; at = text.indexOf(needle, at + 1)) {
    // Do not turn 112 kV into an apparent match for 12 kV, or kVA into kV.
    if (at > 0 && word(needle[0]) && word(text[at - 1])) continue;
    if (/\d/.test(needle[0]) && (/[+−±-]$/.test(text.slice(0, at)) || /\d[.,/]$/.test(text.slice(0, at)))) continue;
    const end = at + needle.length;
    if (/\d/.test(needle[needle.length - 1]) && /^[.,/]\d/.test(text.slice(end))) continue;
    if (end < text.length && word(needle[needle.length - 1]) && word(text[end])) continue;
    matches.push(at);
  }
  // A table-of-contents row (title ........ 40) repeats a heading; prefer the body when the body also has the quote.
  const body = matches.filter(at => !TOC_ROW.test(positions[at].node.parentElement?.closest('p,li,td')?.textContent ?? ''));
  const candidates = body.length ? body : matches;
  if (!candidates.length) return 0;
  if (candidates.length > 1) return 2;
  const at = candidates[0];
  const start = positions[at], end = positions[at + needle.length - 1];
  const nodes = [...new Set(positions.slice(at, at + needle.length).map(position => position.node))];
  for (const part of nodes) {
    const from = part === start.node ? start.offset : 0;
    const to = part === end.node ? end.offset + 1 : part.length;
    if (from >= to) continue;
    const range = root.ownerDocument.createRange();
    range.setStart(part, from); range.setEnd(part, to);
    const mark = root.ownerDocument.createElement('mark');
    mark.dataset.rfpQuote = 'true';
    range.surroundContents(mark);
  }
  return 1;
}

/**
 * Page headers/footers that a converted DOCX keeps as ordinary body text (페이지 7 / 42, 제 3장 기술규격서) split quotes
 * that cross a page. Treat a page-edge block as furniture only when its digit-blind text repeats on at least half the pages.
 */
function runningPageText(root: HTMLElement): Set<Element> {
  const pages = [...root.querySelectorAll('section.docx > article')];
  if (pages.length < 3) return new Set();
  const key = (element: Element) => (element.textContent ?? '').replace(/\d+/g, '#').replace(/\s+/g, '').trim();
  const edges = pages.flatMap(page => {
    const blocks = [...page.children].filter(child => key(child));
    return [...new Set([...blocks.slice(0, 2), ...blocks.slice(-2)])];
  });
  const counts = new Map<string, number>();
  for (const edge of edges) counts.set(key(edge), (counts.get(key(edge)) ?? 0) + 1);
  return new Set(edges.filter(edge => counts.get(key(edge))! >= pages.length / 2));
}

/** Reveal an explicitly selected quote inside its viewer, never scroll outer ancestors. */
export function revealQuote(root: HTMLElement, scroller: HTMLElement) {
  const mark = root.querySelector('mark[data-rfp-quote]');
  if (mark) revealElement(mark, scroller);
}

// 도면처럼 인용문 대신 본문 이미지 n번째를 보여 줄 때(OCR 요구).
export function revealImage(root: HTMLElement, scroller: HTMLElement, n: number) {
  const image = root.querySelectorAll('img')[n - 1] as HTMLElement | undefined;
  if (!image) return false;
  image.style.outline = '3px solid #f2c94c'; image.style.outlineOffset = '2px';
  revealElement(image, scroller);
  return true;
}

function revealElement(mark: Element, scroller: HTMLElement) {
  // A document's scrolling element moves with its content (rect.top = -scrollTop); its viewport starts at 0.
  const viewTop = scroller === scroller.ownerDocument.scrollingElement ? 0 : scroller.getBoundingClientRect().top;
  const top = scroller.scrollTop + mark.getBoundingClientRect().top - viewTop - scroller.clientHeight / 3;
  scroller.scrollTop = Math.max(0, top);
}
