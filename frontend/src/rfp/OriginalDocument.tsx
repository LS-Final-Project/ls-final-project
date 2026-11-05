import { useEffect, useRef, useState } from 'react';
import type { DocumentVersion, RequirementDetail } from '../api/rfp';
import { ApiError } from '../api/client';
import { ErrorState, Loading } from '../components/states';
import { highlightQuote, revealImage, revealQuote } from './document-highlight';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import './pdf-text-layer.css';
import { OCR_CLAUSE } from './labels';

const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const FRAME = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; font-src \'none\'; form-action \'none\'; base-uri \'none\'"><style>body{margin:0;background:#e9edf2} .docx-wrapper{padding:14px!important} section.docx{box-sizing:border-box;max-width:100%;padding:30px!important;min-height:0!important} mark[data-rfp-quote]{background:#fff0a6;color:inherit;text-decoration:underline;text-decoration-color:#d18d00;text-decoration-thickness:2px;text-underline-offset:3px;cursor:pointer} table{max-width:100%} img{max-width:100%}</style></head><body></body></html>';

// 서비스 재시작 직후 등 순간 끊김이면 짧게 두 번 더 받아 본다(0.4초, 1.2초). 권한·형식 오류(4xx)는 바로 알린다.
async function fetchOriginal(document: DocumentVersion, signal: AbortSignal): Promise<Response> {
  const url = `/api/v1/tft/documents/${encodeURIComponent(document.document_id)}/file`;
  for (const wait of [400, 1200, -1]) {
    try {
      const response = await fetch(url, { credentials: 'same-origin', signal, redirect: 'error' });
      if (response.status < 500 || wait < 0) return response;
    } catch (error) {
      if (signal.aborted || wait < 0) throw error;
    }
    await new Promise(done => setTimeout(done, wait));
  }
  throw new Error('unreachable');
}

/** The normal authenticated file route remains authoritative; never fetch third-party document URLs. */
export async function readOriginal(document: DocumentVersion, signal: AbortSignal): Promise<ArrayBuffer> {
  if (![DOCX, 'application/pdf'].includes(document.mime)) throw new Error('이 형식의 원문 미리보기는 지원하지 않습니다.');
  const response = await fetchOriginal(document, signal);
  if (!response.ok) throw new ApiError(response.status, '원문을 읽지 못했습니다. 로그인과 문서 접근 권한을 확인하세요.');
  if (response.headers.get('content-type')?.split(';')[0] !== document.mime) throw new Error('원문 파일 형식이 문서 정보와 다릅니다.');
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > 25 * 1024 * 1024 || bytes.byteLength !== document.size) throw new Error('원문 크기가 문서 정보와 다릅니다. 문서 목록을 새로 고침하세요.');
  const sha = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), n => n.toString(16).padStart(2, '0')).join('');
  if (sha !== document.sha256) throw new Error('원문 판본이 변경되었습니다. 문서 목록을 새로 고침하세요.');
  return bytes;
}

export function originalQuote(origin?: RequirementDetail['origin']) {
  const ocr = origin?.clause_no?.match(OCR_CLAUSE);
  if (!ocr) return { quote: origin?.quote ?? '', notice: undefined, image: undefined };
  const location = ocr[1] === 'DOCX-' ? `본문 이미지 순번 ${Number(ocr[2])}` : `PDF 원문 ${origin?.page ?? Number(ocr[2])}쪽`;
  return { quote: '', notice: `이미지 전사 · ${location}. 원본 이미지와 수치를 대조하세요. 이미지의 글자를 원문 텍스트와 일치했다고 표시하지 않습니다.`, image: ocr[1] === 'DOCX-' ? Number(ocr[2]) : undefined };
}

// onSelectText: 원문에서 드래그로 고른 글. 표 안에서 고르면 빈 글로 알린다(표 글은 조항 텍스트에 없어 요구로 추가할 수 없다).
// 원문 파일은 판본(sha256)마다 한 번만 받는다. 작업 공간은 요구를 고를 때마다 패널을 새로 그려 같은 파일을 다시 받았다
// (40444 흐름 점검: 한 프로젝트 점검에 79번). 실패한 받기는 지워 다시 시도하게 둔다.
const originals = new Map<string, Promise<ArrayBuffer>>();
function cachedOriginal(document: DocumentVersion, retry: boolean) {
  const id = `${document.document_id}:${document.sha256}`;
  if (retry) originals.delete(id);
  let pending = originals.get(id);
  if (!pending) {
    pending = readOriginal(document, new AbortController().signal);
    originals.set(id, pending);
    pending.catch(() => originals.delete(id));
  }
  // PDF 해석기가 버퍼를 옮겨(detach) 쓸 수 있어 화면마다 사본을 준다.
  return pending.then(bytes => bytes.slice(0));
}

export function OriginalDocument({ document, origin, onQuoteClick, selectionRevision = 0, onSelectText }: { document: DocumentVersion; origin?: RequirementDetail['origin']; selectionRevision?: number; onQuoteClick: () => void; onSelectText?: (text: string) => void }) {
  const { quote, notice, image } = originalQuote(origin);
  const [bytes, setBytes] = useState<ArrayBuffer>();
  const [error, setError] = useState<unknown>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setBytes(undefined); setError(undefined);
    cachedOriginal(document, attempt > 0).then(value => { if (!controller.signal.aborted) setBytes(value); }).catch(reason => { if (!controller.signal.aborted) setError(reason); });
    return () => controller.abort();
  }, [document.document_id, document.sha256, document.size, document.mime, attempt]);
  return <section className="rfp-original-pane" aria-label="실제 원문 문서">
    <header><div><b>{document.title}</b><small>판본 {document.version} · {document.mime === DOCX ? 'DOCX' : 'PDF'}</small></div><a className="kb" href={`/api/v1/tft/documents/${encodeURIComponent(document.document_id)}/file`}>선택 문서 내려받기</a></header>
    <p className="rfp-review-help">{document.mime === DOCX ? '업로드한 DOCX의 본문·표입니다. Word와 글꼴·페이지 나눔은 다를 수 있습니다.' : '업로드한 PDF입니다. 스캔 이미지처럼 문자가 없는 페이지는 밑줄 위치를 확인할 수 없습니다.'}</p>
    {error ? <ErrorState error={error} onRetry={() => setAttempt(attempt + 1)} /> : !bytes ? <Loading label="원문 파일 읽는 중" /> : document.mime === DOCX ? <DocxOriginal bytes={bytes} quote={quote} notice={notice} image={image} selectionRevision={selectionRevision} onQuoteClick={onQuoteClick} onSelectText={onSelectText} /> : <PdfOriginal bytes={bytes} quote={quote} notice={notice} sourcePage={origin?.page} selectionRevision={selectionRevision} onQuoteClick={onQuoteClick} onSelectText={onSelectText} />}
  </section>;
}

function MatchNotice({ count, quote, notice }: { count?: number; quote: string; notice?: string }) {
  return <p className="rfp-match-notice" role="status">{notice ?? (!quote ? '분석한 요구를 고르면 그 인용문을 원문에 강조합니다.' : count === undefined ? '인용 위치 확인 중' : count === 1 ? '선택한 요구의 인용문을 원문에 강조했습니다.' : count === 2 ? '같은 인용문이 여러 곳에 있습니다. 조항을 대조해 위치를 확인하세요.' : '인용 위치 확인 필요 — 인용문과 원문을 대조하세요.')}</p>;
}

function DocxOriginal({ bytes, quote, notice, image, onQuoteClick, selectionRevision, onSelectText }: { bytes: ArrayBuffer; quote: string; notice?: string; image?: number; selectionRevision: number; onQuoteClick: () => void; onSelectText?: (text: string) => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const revealed = useRef(0);
  const [ready, setReady] = useState(0);
  const [rendered, setRendered] = useState(false);
  const [error, setError] = useState<unknown>();
  const [match, setMatch] = useState<number>();
  useEffect(() => {
    const body = frame.current?.contentDocument?.body;
    if (!ready || !body) return;
    let cancelled = false; setRendered(false); setError(undefined);
    const content = body.ownerDocument.createElement('div');
    body.inert = true;
    body.replaceChildren(content);
    import('docx-preview').then(async ({ renderAsync }) => {
      await renderAsync(bytes, content, content, { renderAltChunks: false, ignoreFonts: true, useBase64URL: true, renderChanges: false, ignoreLastRenderedPageBreak: false, breakPages: true });
      if (cancelled) return;
      // Untrusted document stays in a no-script, no-network iframe. Links/forms are inert.
      for (const element of content.querySelectorAll('a')) element.removeAttribute('href');
      for (const element of content.querySelectorAll('input,button,select,textarea')) element.setAttribute('disabled', '');
      body.inert = false;
      setRendered(true);
    }).catch(reason => { if (!cancelled) setError(reason); });
    return () => { cancelled = true; content.remove(); };
  }, [bytes, ready]);
  useEffect(() => {
    const body = frame.current?.contentDocument?.body;
    setMatch(undefined);
    if (!rendered || !body) return;
    setMatch(highlightQuote(body, quote));
  }, [quote, rendered]);
  useEffect(() => {
    const body = frame.current?.contentDocument?.body;
    const scroller = frame.current?.contentDocument?.scrollingElement as HTMLElement | null;
    if (!rendered || !body || !scroller || (!quote && !image) || selectionRevision <= revealed.current) return;
    if (quote) revealQuote(body, scroller); else revealImage(body, scroller, image as number);
    revealed.current = selectionRevision;
  }, [quote, image, rendered, selectionRevision]);
  useEffect(() => {
    const body = frame.current?.contentDocument?.body;
    if (!rendered || !body) return;
    const click = (e: MouseEvent) => { if ((e.target as Element)?.closest?.('mark[data-rfp-quote]')) onQuoteClick(); };
    const select = () => onSelectText?.(selectedText(body.ownerDocument.getSelection()));
    body.addEventListener('click', click);
    body.addEventListener('mouseup', select);
    return () => { body.removeEventListener('click', click); body.removeEventListener('mouseup', select); };
  }, [rendered, onQuoteClick, onSelectText]);
  return <><MatchNotice count={match} quote={quote} notice={notice} />{error && <ErrorState error={error} />}{!rendered && !error && <Loading label="문서 화면 준비 중" />}<iframe ref={frame} className="rfp-docx-frame" title="DOCX 원문" sandbox="allow-same-origin" srcDoc={FRAME} onLoad={() => setReady(n => n + 1)} /></>;
}

function PdfOriginal({ bytes, quote, notice, sourcePage, onQuoteClick, selectionRevision, onSelectText }: { bytes: ArrayBuffer; quote: string; notice?: string; sourcePage?: number | null; selectionRevision: number; onQuoteClick: () => void; onSelectText?: (text: string) => void }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy>();
  const [page, setPage] = useState(1);
  const [error, setError] = useState<unknown>();
  const [match, setMatch] = useState<number>();
  const [rendered, setRendered] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const layer = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const [renderedQuote, setRenderedQuote] = useState<string>();
  const revealed = useRef(0);
  useEffect(() => {
    let cancelled = false;
    let task: ReturnType<typeof import('pdfjs-dist')['getDocument']> | undefined;
    setPdf(undefined); setError(undefined);
    import('pdfjs-dist').then(lib => {
      if (cancelled) return;
      lib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
      task = lib.getDocument({ data: bytes.slice(0), enableXfa: false, useSystemFonts: true });
      return task.promise.then(value => { if (!cancelled) setPdf(value); });
    }).catch(reason => { if (!cancelled) setError(reason); });
    return () => { cancelled = true; void task?.destroy(); };
  }, [bytes]);
  // 요구를 고르면 그 쪽으로 넘기고 칸 맨 위부터 보인다(인용문이 있으면 그려진 뒤 그 위치로 다시 내린다).
  useEffect(() => { setPage(sourcePage && pdf && sourcePage > 0 && sourcePage <= pdf.numPages ? sourcePage : 1); scroller.current?.scrollTo(0, 0); }, [sourcePage, quote, pdf]);
  useEffect(() => {
    if (!pdf || !canvas.current || !layer.current) return;
    let cancelled = false;
    let render: ReturnType<import('pdfjs-dist').PDFPageProxy['render']> | undefined;
    let text: import('pdfjs-dist').TextLayer | undefined;
    const target = canvas.current, host = layer.current;
    setRendered(false); setMatch(undefined); setError(undefined); host.replaceChildren();
    (async () => {
      const docPage = await pdf.getPage(page);
      if (cancelled) return;
      const viewport = docPage.getViewport({ scale: 1.1 });
      // Bound the canvas for documents with unusually large page dimensions.
      if (viewport.width * viewport.height > 16_000_000) throw new Error('페이지 크기가 너무 커 미리보기를 표시할 수 없습니다.');
      target.width = Math.ceil(viewport.width); target.height = Math.ceil(viewport.height);
      host.style.setProperty('--scale-factor', String(viewport.scale));
      host.style.setProperty('--total-scale-factor', String(viewport.scale));
      render = docPage.render({ canvas: target, viewport });
      await render.promise;
      const { TextLayer } = await import('pdfjs-dist');
      const content = await docPage.getTextContent();
      if (cancelled) return;
      text = new TextLayer({ textContentSource: content, container: host, viewport });
      await text.render();
      if (!cancelled) { setRenderedQuote(quote); setRendered(true); setMatch(highlightQuote(host, quote)); }
    })().catch(reason => { if (!cancelled) setError(reason); });
    return () => { cancelled = true; render?.cancel(); text?.cancel(); };
  }, [pdf, page, quote]);
  useEffect(() => {
    if (!rendered || renderedQuote !== quote || !layer.current || !scroller.current || !quote || selectionRevision <= revealed.current) return;
    revealQuote(layer.current, scroller.current); revealed.current = selectionRevision;
  }, [quote, rendered, renderedQuote, selectionRevision]);
  return <><div className="rfp-pdf-navigation"><button className="kb" disabled={!pdf || page <= 1} onClick={() => setPage(page - 1)}>이전 원문 쪽</button><span>{page} / {pdf?.numPages ?? '…'}</span><button className="kb" disabled={!pdf || page >= pdf.numPages} onClick={() => setPage(page + 1)}>다음 원문 쪽</button></div><MatchNotice count={match} quote={quote} notice={notice} />{error && <ErrorState error={error} />}{!rendered && !error && <Loading label="PDF 페이지 준비 중" />}<div className="rfp-pdf-scroll" ref={scroller}><div className="rfp-pdf-page"><canvas ref={canvas} aria-label={`원문 ${page}쪽`} /><div ref={layer} className="textLayer" onMouseUp={() => onSelectText?.(selectedText(window.getSelection()))} onClick={event => { if ((event.target as Element).closest('mark[data-rfp-quote]')) onQuoteClick(); }} /></div></div></>;
}

// 고른 글. 표 칸 안에서 시작하거나 끝나면 빈 글이다.
export function selectedText(selection: Selection | null) {
  if (!selection || selection.isCollapsed) return '';
  const inTable = (node: Node | null) => !!(node instanceof Element ? node : node?.parentElement)?.closest('td,th,table');
  if (inTable(selection.anchorNode) || inTable(selection.focusNode)) return '';
  return selection.toString().replace(/\s+/g, ' ').trim();
}
