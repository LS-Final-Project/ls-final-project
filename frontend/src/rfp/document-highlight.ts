/** Match only a unique literal quote, allowing layout whitespace but no fuzzy/value changes. */
export function highlightQuote(root: HTMLElement, quote: string): number {
  for (const mark of root.querySelectorAll('mark[data-rfp-quote]')) mark.replaceWith(...mark.childNodes);
  root.normalize();
  const needle = quote.replace(/\s+/g, ' ').trim();
  if (!needle) return 0;
  const walker = root.ownerDocument.createTreeWalker(root, 5);
  const positions: { node: Text; offset: number }[] = [];
  let text = '', previousBlock: Element | null = null, lineBreak = false;
  let node: Node | null;
  while ((node = walker.nextNode())) {
    if (node.nodeType === 1) {
      if ((node as Element).tagName === 'BR' && !(node as Element).closest('del,[aria-hidden="true"]')) lineBreak = true;
      continue;
    }
    const parent = node.parentElement;
    if (!parent || parent.closest('style,script,del,[aria-hidden="true"]')) continue;
    const block = parent.closest('p,td,li');
    if ((lineBreak || previousBlock && block !== previousBlock) && text && !text.endsWith(' ')) {
      text += ' '; positions.push({ node: node as Text, offset: 0 });
    }
    previousBlock = block; lineBreak = false;
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
  if (!matches.length) return 0;
  if (matches.length > 1) return 2;
  const at = matches[0];
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
  root.querySelector('mark[data-rfp-quote]')?.scrollIntoView?.({ block: 'center', behavior: 'instant' });
  return 1;
}
