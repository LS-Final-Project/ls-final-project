import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { ApiError, getApiMode } from '../api/client';
import { listDocumentDiagrams, type DiagramItem, type DocumentVersion } from '../api/rfp';
import { ErrorState, Loading } from '../components/states';
import './DiagramReview.css';

type GraphResponse = NonNullable<DiagramItem['graph']>;
type NodeResponse = NonNullable<GraphResponse['nodes']>[number];
type EdgeResponse = NonNullable<GraphResponse['edges']>[number];
type Node = Omit<NodeResponse, 'ratings' | 'uncertainties'> & { ratings: string[]; uncertainties: string[] };
type Edge = Omit<EdgeResponse, 'path' | 'uncertainties' | 'target'> & { path: NonNullable<EdgeResponse['path']>; uncertainties: string[]; target: string | null };
type Graph = Omit<GraphResponse, 'nodes' | 'edges' | 'uncertainties'> & { nodes: Node[]; edges: Edge[]; uncertainties: string[] };
// These optional fields have empty-list defaults in the API's typed diagram contract.
const diagramGraph = (graph: GraphResponse): Graph => ({ ...graph, nodes: (graph.nodes ?? []).map(node => ({ ...node, ratings: node.ratings ?? [], uncertainties: node.uncertainties ?? [] })), edges: (graph.edges ?? []).map(edge => ({ ...edge, target: edge.target ?? null, path: edge.path ?? [], uncertainties: edge.uncertainties ?? [] })), uncertainties: graph.uncertainties ?? [] });
const IMAGE_MIMES = ['image/png', 'image/jpeg', 'image/webp', 'application/pdf'];
const MAX_BYTES = 8 * 1024 * 1024;
const KINDS: Record<Node['kind'], string> = { TRANSFORMER: '변압기', BREAKER: '차단기', BUS: '모선', DISCONNECTOR: '단로기', FUSE: '퓨즈', LOAD: '부하', GENERATOR: '발전기', GROUND: '접지', METER: '계기', TERMINAL: '단자', OTHER: '기타', UNKNOWN: '기호 미확인' };
const CONNECTIONS: Record<Edge['connection'], string> = { CONTINUOUS_LINE: '연속선 관측', JUNCTION_DOT: '접속점 관측', UNRESOLVED: '연결 불명확', CROSS_PAGE: '다른 쪽 참조 · 미연결' };
const nodeLabel = (node: Node) => node.tag ? `${node.tag} · ${node.label}` : node.label;

/** Fetch only this authorized document/version/source; preserve the original bytes without recompression. */
export async function readDiagramAsset(document: DocumentVersion, item: DiagramItem, signal: AbortSignal): Promise<ArrayBuffer> {
  if (!IMAGE_MIMES.includes(item.mime) || !Number.isInteger(item.size) || item.size < 1 || item.size > MAX_BYTES || !/^[0-9a-f]{64}$/.test(item.sha256)) throw new Error('도면 원본 메타데이터를 확인할 수 없습니다.');
  const path = `/api/v1/tft/documents/${encodeURIComponent(document.document_id)}/diagrams/${encodeURIComponent(item.source_id)}/image?${new URLSearchParams({ document_version_id: document.document_version_id })}`;
  const response = await fetch(path, { credentials: 'same-origin', redirect: 'error', signal });
  if (!response.ok) throw new ApiError(response.status, '도면 원본을 읽지 못했습니다. 문서 접근 권한을 확인하세요.');
  if (response.headers.get('content-type')?.split(';')[0].trim() !== item.mime) throw new Error('도면 원본 형식이 분석 정보와 다릅니다.');
  const bytes = await response.arrayBuffer();
  signal.throwIfAborted();
  if (bytes.byteLength !== item.size || bytes.byteLength > MAX_BYTES) throw new Error('도면 원본 크기가 분석 정보와 다릅니다.');
  const sha = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), n => n.toString(16).padStart(2, '0')).join('');
  signal.throwIfAborted();
  if (sha !== item.sha256) throw new Error('도면 원본이 분석 정보와 일치하지 않습니다. 다시 조회하세요.');
  return bytes;
}

export function DiagramReview({ document, actorId, projectId, onOriginalVisible }: { document: DocumentVersion; actorId?: string; projectId: string; onOriginalVisible?: (visible: boolean) => void }) {
  const [open, setOpen] = useState(document.kind === 'DRAWING');
  return <details className="rfp-diagram-review" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>단선결선도 연결 검토<small>원본 이미지와 AI 연결 후보 대조</small></summary>
    {open && <DiagramBody key={`${actorId}:${document.document_version_id}`} document={document} actorId={actorId} projectId={projectId} onOriginalVisible={onOriginalVisible} />}
  </details>;
}

function DiagramBody({ document, actorId, projectId, onOriginalVisible }: { document: DocumentVersion; actorId?: string; projectId: string; onOriginalVisible?: (visible: boolean) => void }) {
  const [source, setSource] = useState<string>();
  const query = useQuery({ queryKey: ['rfp-diagrams', getApiMode(), actorId, projectId, document.document_id, document.document_version_id], queryFn: ({ signal }) => listDocumentDiagrams(document.document_id, document.document_version_id, signal), retry: false });
  if (query.isPending) return <Loading label="현재 판본의 도면 분석 읽는 중" />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const data = query.data.data;
  if (data.document_id !== document.document_id || data.document_version_id !== document.document_version_id) return <p role="alert" className="rfp-review-warning">도면 분석과 선택 문서 판본이 다릅니다. 문서 목록을 새로 고침하세요.</p>;
  if (data.processing_status === 'NOT_ANALYZED') return <p className="rfp-diagram-empty">이 판본에는 단선결선도 분석 기록이 없습니다. 기존 분석 결과를 자동으로 다시 처리하지 않습니다.</p>;
  const nodes = data.items.reduce((total, row) => total + (row.graph?.nodes?.length ?? 0), 0);
  const edges = data.items.reduce((total, row) => total + (row.graph?.edges?.length ?? 0), 0);
  const item = data.items.find(row => row.source_id === source) ?? data.items[0];
  return <div className="rfp-diagram-body">
    <section className="rfp-diagram-result-summary" aria-label="도면 분석 결과 요약">
      <strong>{data.processing_status === 'PARTIAL' ? '도면 분석 기록 · 확인 필요' : '저장된 도면 분석 기록'}</strong>
      <div><span>도면 원본 {data.items.length}개</span><span>장비·기호 후보 {nodes}개</span><span>연결 후보 {edges}개</span></div>
      <p>문자 요구 개수와 별도로 집계한 검토 후보입니다. 연결과 정격은 원본 확인이 필요합니다.</p>
    </section>
    <p className="rfp-diagram-intro">장비와 연결선은 AI가 읽은 검토 후보입니다. 접속점·교차선·표시 정격을 원본과 대조하세요. 연결 후보만으로 전기적 적합성이나 자사·협력사 대체 가능을 확정하지 않습니다.</p>
    {data.warnings.length > 0 && <div className="rfp-diagram-notices" role="status"><h4>원문 확인 필요</h4><ul>{data.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul></div>}
    {!item ? <p className="rfp-diagram-empty">{data.processing_status === 'PARTIAL' ? '도면 분석 일부를 확인하지 못했습니다. 원문 확인 안내를 살펴보세요.' : '이 판본에서 저장된 단선결선도 연결 후보가 없습니다. 도면이 없거나 읽지 못한 경우를 원문과 대조하세요.'}</p> : <>
      <div className="rfp-diagram-toolbar"><label>검토 도면<select aria-label="검토 도면" value={item.source_id} onChange={event => setSource(event.target.value)}>{data.items.map(row => <option key={row.source_id} value={row.source_id}>{row.location}{row.graph?.title ? ` · ${row.graph.title}` : ''}</option>)}</select><span>문서 판본 v{document.version} · 원본 이미지 보존</span></label></div>
      <DiagramPanel key={`${document.document_version_id}:${item.source_id}:${item.sha256}`} document={document} item={item} projectId={projectId} onOriginalVisible={onOriginalVisible} />
    </>}
  </div>;
}

function DiagramPanel({ document, item, projectId, onOriginalVisible }: { document: DocumentVersion; item: DiagramItem; projectId: string; onOriginalVisible?: (visible: boolean) => void }) {
  const [asset, setAsset] = useState<{ bytes: ArrayBuffer; url: string }>();
  const [error, setError] = useState<unknown>();
  const [attempt, setAttempt] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [selection, setSelection] = useState<{ node?: string; edge?: string }>({});
  const [painted, setPainted] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let url: string | undefined;
    setAsset(undefined); setError(undefined); setPainted(false);
    readDiagramAsset(document, item, controller.signal).then(bytes => {
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(new Blob([bytes], { type: item.mime }));
      setAsset({ bytes, url });
    }).catch(reason => { if (!controller.signal.aborted) setError(reason); });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [document.document_id, document.document_version_id, item, attempt]);
  useEffect(() => {
    onOriginalVisible?.(painted && !error);
    return () => onOriginalVisible?.(false);
  }, [painted, error, onOriginalVisible]);
  if (error && !asset) return <ErrorState error={error} onRetry={() => setAttempt(attempt + 1)} />;
  if (!asset) return <Loading label="보존된 도면 원본 확인 중" />;
  const graph = item.graph ? diagramGraph(item.graph) : null;
  const node = graph?.nodes.find(row => row.id === selection.node);
  const edge = graph?.edges.find(row => row.id === selection.edge);
  const extension = item.mime === 'application/pdf' ? 'pdf' : item.mime === 'image/jpeg' ? 'jpg' : item.mime === 'image/webp' ? 'webp' : 'png';
  return <>
    <div className="rfp-diagram-toolbar"><span>{item.location}{item.page != null ? ` · 원문 ${item.page}쪽` : ''}</span><div className="rfp-diagram-tools"><button className="kb" disabled={zoom <= 1} onClick={() => setZoom(value => Math.max(1, value - .5))}>축소</button><output aria-label="도면 확대 비율">{Math.round(zoom * 100)}%</output><button className="kb" disabled={zoom >= 4} onClick={() => setZoom(value => Math.min(4, value + .5))}>확대</button><button className="kb" onClick={() => setZoom(1)}>맞춤</button><a className="kb" href={asset.url} download={`${item.source_id}.${extension}`}>도면 원본 내려받기</a></div></div>
    <div className="rfp-diagram-layout">
      <div className="rfp-diagram-source">{error ? <ErrorState error={error} onRetry={() => setAttempt(attempt + 1)} /> : null}<div className="rfp-diagram-scroll" hidden={!!error} aria-label="도면 원본 확대 영역"><div className="rfp-diagram-stage" style={{ width: `${zoom * 100}%` }}>
        {item.mime === 'application/pdf' ? <DiagramPdf bytes={asset.bytes} onReady={() => setPainted(true)} onError={setError} /> : <img src={asset.url} alt={`${item.location} 보존 원본`} onLoad={() => setPainted(true)} onError={() => setError(new Error('도면 이미지 표시를 완료하지 못했습니다.'))} />}
        {painted && !error && graph && <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="AI 추정 위치 · 연결 확정 아님">
          {graph.diagram_type === 'SINGLE_LINE' && graph.edges.filter(row => row.path.length > 1).map(row => <polyline key={row.id} className="rfp-diagram-edge" data-selected={edge?.id === row.id || !!node && (row.source === node.id || row.target === node.id)} points={row.path.map(point => `${point.x},${point.y}`).join(' ')} />)}
          {graph.nodes.filter(row => row.bbox != null).map(row => { const box = row.bbox!; return <rect key={row.id} className="rfp-diagram-node" role="button" tabIndex={0} aria-label={`도면 위치 ${nodeLabel(row)} · 후보`} aria-pressed={node?.id === row.id} x={box.x_min} y={box.y_min} width={box.x_max - box.x_min} height={box.y_max - box.y_min} onClick={() => setSelection({ node: row.id })} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelection({ node: row.id }); } }} />; })}
        </svg>}
      </div></div><p className="rfp-diagram-metadata">점선 표시는 AI가 추정한 위치이며 실제 측정 좌표가 아닙니다. 확대는 표시만 바꾸며 내려받는 원본 바이트는 유지합니다.</p><details className="rfp-diagram-metadata"><summary>판본·원본 확인 정보</summary><p>{document.title} · v{document.version}<br />{item.location} · {item.size.toLocaleString()} bytes<br />SHA-256 {item.sha256}</p></details></div>
      {!graph ? <p className="rfp-diagram-empty">이 이미지의 연결 구조를 확인하지 못했습니다. 원문 확인 안내를 살펴보세요.</p> : <div className="rfp-diagram-sidebar">
        <p className="rfp-diagram-intro">{graph.diagram_type === 'SINGLE_LINE' ? '단선결선도 관측 · 검토 필요' : graph.diagram_type === 'OTHER' ? '다른 유형의 도면 · 연결 해석 대상 아님' : '도면 유형 미확인 · 연결 해석 보류'}</p>
        <h4>장비·기호 후보 {graph.nodes.length}개</h4><div className="rfp-diagram-choice-list" role="group" aria-label="도면 장비 선택">{graph.nodes.map(row => <button key={row.id} className="rfp-diagram-choice" aria-pressed={node?.id === row.id} onClick={() => setSelection({ node: row.id })}><b>{nodeLabel(row)}</b><small>{KINDS[row.kind]} · {row.bbox ? '위치 대조 필요' : '위치 미확인'}</small></button>)}</div>
        <h4>연결 후보 {graph.edges.length}개</h4><div className="rfp-diagram-choice-list" role="group" aria-label="도면 연결 선택">{graph.edges.map(row => <button key={row.id} className="rfp-diagram-choice" aria-pressed={edge?.id === row.id} onClick={() => setSelection({ edge: row.id })}>{edgeLabel(row, graph)}<small>{CONNECTIONS[row.connection]}</small></button>)}</div>
        {!graph.edges.length && <p className="rfp-diagram-intro">저장된 연결 후보가 없습니다. 연결이 없다는 확정은 아닙니다.</p>}
        {node && <NodeDetail node={node} graph={graph} item={item} projectId={projectId} onEdge={id => setSelection({ edge: id })} />}
        {edge && <EdgeDetail edge={edge} graph={graph} onNode={id => setSelection({ node: id })} />}
        {!node && !edge && <p className="rfp-diagram-empty">원본 위 장비 표시나 목록을 선택하면 근거와 연결 후보를 확인할 수 있습니다.</p>}
        {graph.uncertainties.length > 0 && <div className="rfp-diagram-notices"><h4>불확실·누락 가능 영역</h4><ul>{graph.uncertainties.map((value, i) => <li key={i}>{value}</li>)}</ul></div>}
      </div>}
    </div>
  </>;
}

function edgeLabel(edge: Edge, graph: Graph) {
  const label = (id: string | null) => graph.nodes.find(node => node.id === id)?.tag || graph.nodes.find(node => node.id === id)?.label || '끝점 미확인';
  return `${label(edge.source)} — ${edge.connection === 'CROSS_PAGE' ? edge.off_page_reference ?? '다른 쪽' : label(edge.target)}`;
}

function NodeDetail({ node, graph, item, projectId, onEdge }: { node: Node; graph: Graph; item: DiagramItem; projectId: string; onEdge: (id: string) => void }) {
  const related = graph.edges.filter(edge => edge.source === node.id || edge.target === node.id);
  const requirements = item.requirement_links.filter(link => link.node_id === node.id);
  return <section className="rfp-diagram-detail" aria-label="선택 장비 도면 근거"><h4>{nodeLabel(node)}</h4><dl><dt>관측 근거</dt><dd>{node.evidence}</dd><dt>표시 정격</dt><dd>{node.ratings.length ? node.ratings.join(' / ') : '명확히 연결된 정격 표시 없음'}</dd></dl>{node.uncertainties.length > 0 && <ul>{node.uncertainties.map((value, i) => <li key={i}>{value}</li>)}</ul>}<h4>연결된 후보</h4>{related.length ? <ul>{related.map(edge => <li key={edge.id}><button className="kb" onClick={() => onEdge(edge.id)}>{edgeLabel(edge, graph)} · {CONNECTIONS[edge.connection]}</button></li>)}</ul> : <p>저장된 연결 후보 없음</p>}<h4>같은 태그의 요구 후보</h4><p>태그 일치만으로 동일 설비나 대응 가능을 확정하지 않습니다.</p>{requirements.length ? <ul>{requirements.map(link => <li key={`${link.requirement_id}:${link.requirement_version}`}><Link to={`/tft/projects/${projectId}/requirements/${link.requirement_id}`}>{link.code} · 요구 v{link.requirement_version}{link.equipment ? ` · ${link.equipment}` : ''}</Link></li>)}</ul> : <p>연결된 요구 후보가 없습니다. 본문 요구와 장비 태그를 대조하세요.</p>}</section>;
}

function EdgeDetail({ edge, graph, onNode }: { edge: Edge; graph: Graph; onNode: (id: string) => void }) {
  return <section className="rfp-diagram-detail" aria-label="선택 연결 도면 근거"><h4>{edgeLabel(edge, graph)}</h4><p>{CONNECTIONS[edge.connection]} · 확인 전 연결 후보</p><p>{edge.evidence}</p>{edge.off_page_reference && <p>인쇄된 참조: {edge.off_page_reference} · 다른 쪽과 자동 연결하지 않았습니다.</p>}{edge.uncertainties.length > 0 && <ul>{edge.uncertainties.map((value, i) => <li key={i}>{value}</li>)}</ul>}<div><button className="kb" onClick={() => onNode(edge.source)}>시작 장비 확인</button>{edge.target && <button className="kb" onClick={() => onNode(edge.target!)}>상대 장비 확인</button>}</div></section>;
}

function DiagramPdf({ bytes, onReady, onError }: { bytes: ArrayBuffer; onReady: () => void; onError: (error: unknown) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const onReadyRef = useRef(onReady), onErrorRef = useRef(onError);
  onReadyRef.current = onReady; onErrorRef.current = onError;
  useEffect(() => {
    let cancelled = false;
    let task: ReturnType<typeof import('pdfjs-dist')['getDocument']> | undefined;
    let document: PDFDocumentProxy | undefined;
    let render: ReturnType<import('pdfjs-dist').PDFPageProxy['render']> | undefined;
    (async () => {
      const lib = await import('pdfjs-dist');
      if (cancelled) return;
      lib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
      task = lib.getDocument({ data: bytes.slice(0), enableXfa: false, useSystemFonts: true });
      document = await task.promise;
      if (cancelled) return;
      if (document.numPages !== 1) throw new Error('선택 도면의 단일 원문 페이지를 확인할 수 없습니다.');
      const page = await document.getPage(1);
      if (cancelled || !canvas.current) return;
      const viewport = page.getViewport({ scale: 1.5 });
      if (viewport.width * viewport.height > 16_000_000) throw new Error('도면 크기가 너무 커 화면에 표시할 수 없습니다. 원본을 내려받아 확인하세요.');
      canvas.current.width = Math.ceil(viewport.width); canvas.current.height = Math.ceil(viewport.height);
      render = page.render({ canvas: canvas.current, viewport });
      await render.promise;
      if (!cancelled) onReadyRef.current();
    })().catch(error => { if (!cancelled) onErrorRef.current(error); });
    return () => { cancelled = true; render?.cancel(); void task?.destroy(); };
  }, [bytes]);
  return <canvas ref={canvas} aria-label="보존된 PDF 도면 원문 페이지" />;
}
