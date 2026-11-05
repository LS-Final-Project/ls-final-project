// @vitest-environment jsdom
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DiagramReview, readDiagramAsset } from './DiagramReview';
import { listDocumentDiagrams, type DiagramItem, type DiagramList, type DocumentVersion } from '../api/rfp';
vi.mock('../api/rfp', async original => ({ ...await original<object>(), listDocumentDiagrams: vi.fn() }));
const pdf = vi.hoisted(() => ({ getDocument: vi.fn(), render: vi.fn(), destroy: vi.fn(), cancel: vi.fn() }));
vi.mock('pdfjs-dist', () => ({ GlobalWorkerOptions: {}, getDocument: pdf.getDocument }));
const document = { document_id:'doc1',document_version_id:'version1',version:2,title:'합성 도면' } as DocumentVersion;
const bytes = new Uint8Array([137,80,78,71,13,10,26,10]);
let item: DiagramItem;
let response: DiagramList;
const fetchAsset = vi.fn();
const createUrl = vi.fn(), revokeUrl = vi.fn();
const show = (doc = document, onOriginalVisible?: (visible: boolean) => void) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry:false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><DiagramReview document={doc} actorId="actor1" projectId="project1" onOriginalVisible={onOriginalVisible} /></MemoryRouter></QueryClientProvider>);
};
const open = () => { const details = screen.getByText('단선결선도 연결 검토').closest('details')!; details.open = true; fireEvent(details, new Event('toggle')); };
const sha = async (value: Uint8Array) => Buffer.from(await webcrypto.subtle.digest('SHA-256', value)).toString('hex');
const assetResponse = (mime = 'image/png', value = bytes) => new Response(value, { status:200,headers:{'Content-Type':mime} });
beforeEach(async () => {
  vi.stubGlobal('crypto', webcrypto);
  vi.stubGlobal('fetch', fetchAsset);
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL:createUrl, revokeObjectURL:revokeUrl }));
  createUrl.mockReturnValue('blob:original-diagram');
  fetchAsset.mockImplementation(async () => assetResponse());
  item = { source_id:'DOCX-IMAGE-0001',location:'DOCX 이미지 1',page:null,mime:'image/png',sha256:await sha(bytes),size:bytes.length,
    graph:{ diagram_type:'SINGLE_LINE',title:'수전 단선결선도',review_required:true,
      nodes:[{id:'n1',tag:'TR-1',label:'수전 변압기',kind:'TRANSFORMER',bbox:{x_min:100,y_min:200,x_max:300,y_max:400},ratings:['22.9/0.38 kV'],evidence:'TR-1 표시와 변압기 기호',uncertainties:[]},{id:'n2',tag:'CB-1',label:'차단기',kind:'BREAKER',bbox:null,ratings:[],evidence:'CB-1 표시',uncertainties:['정격 글자 불명확']}],
      edges:[{id:'e1',source:'n1',target:'n2',connection:'UNRESOLVED',path:[{x:200,y:400},{x:200,y:700}],evidence:'교차 지점의 접속점 불명확',off_page_reference:null,uncertainties:['교차선 여부 확인']},{id:'e2',source:'n2',target:null,connection:'CROSS_PAGE',path:[],evidence:'다음 페이지 참조 화살표',off_page_reference:'DWG-02 / A1',uncertainties:[]}],uncertainties:['표시하지 않은 선이 있을 수 있음']},
    requirement_links:[{node_id:'n1',requirement_id:'req1',requirement_version:3,code:'R-001',equipment:'TR-1'}] };
  response = { document_id:'doc1',document_version_id:'version1',processing_status:'ANALYZED',items:[item],warnings:[] };
  vi.mocked(listDocumentDiagrams).mockImplementation(async () => ({data:response}));
  pdf.render.mockReturnValue({promise:Promise.resolve(),cancel:pdf.cancel});
  pdf.getDocument.mockReturnValue({promise:Promise.resolve({numPages:1,getPage:async () => ({getViewport:() => ({width:600,height:800}),render:pdf.render})}),destroy:pdf.destroy});
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it('접힌 상태는 추가 조회하지 않고 현재 판본만 요청해 원본·후보를 대조한다', async () => {
  const { container } = show();
  expect(listDocumentDiagrams).not.toHaveBeenCalled(); expect(fetchAsset).not.toHaveBeenCalled();
  open();
  const image = await screen.findByRole('img', {name:'DOCX 이미지 1 보존 원본'});
  expect(listDocumentDiagrams).toHaveBeenCalledWith('doc1','version1',expect.any(AbortSignal));
  expect(fetchAsset).toHaveBeenCalledWith('/api/v1/tft/documents/doc1/diagrams/DOCX-IMAGE-0001/image?document_version_id=version1',expect.objectContaining({credentials:'same-origin',redirect:'error'}));
  expect(container.querySelector('rect')).toBeNull();
  fireEvent.load(image);
  const overlay = await screen.findByRole('button', {name:'도면 위치 TR-1 · 수전 변압기 · 후보'});
  expect(overlay.getAttribute('x')).toBe('100'); expect(overlay.getAttribute('height')).toBe('200');
  expect(container.querySelector('polyline')?.getAttribute('points')).toBe('200,400 200,700');
  fireEvent.keyDown(overlay,{key:'Enter'});
  const detail = screen.getByRole('region',{name:'선택 장비 도면 근거'});
  expect(within(detail).getByText('22.9/0.38 kV')).toBeTruthy();
  expect(within(detail).getByRole('link',{name:'R-001 · 요구 v3 · TR-1'}).getAttribute('href')).toBe('/tft/projects/project1/requirements/req1');
  expect(within(detail).getByText('태그 일치만으로 동일 설비나 대응 가능을 확정하지 않습니다.')).toBeTruthy();
  expect(container.querySelector('polyline')?.getAttribute('data-selected')).toBe('true');
  fireEvent.click(screen.getByRole('button',{name:'확대'}));
  expect(screen.getByLabelText('도면 확대 비율').textContent).toBe('150%');
  expect(screen.getByRole('link',{name:'도면 원본 내려받기'}).getAttribute('download')).toBe('DOCX-IMAGE-0001.png');
  expect(fetchAsset).toHaveBeenCalledTimes(1);
  expect(createUrl.mock.calls[0][0].size).toBe(bytes.length);
});

it('불명확 교차선과 다른 쪽 참조를 후보로 표시하고 자동 연결·확정하지 않는다', async () => {
  show(); open(); await screen.findByRole('img');
  const choices = screen.getByRole('group',{name:'도면 연결 선택'});
  fireEvent.click(within(choices).getByRole('button',{name:/TR-1 — CB-1/}));
  const uncertain = screen.getByRole('region',{name:'선택 연결 도면 근거'});
  expect(within(uncertain).getByText('교차 지점의 접속점 불명확')).toBeTruthy();
  expect(within(uncertain).getByText('연결 불명확 · 확인 전 연결 후보')).toBeTruthy();
  fireEvent.click(within(choices).getByRole('button',{name:/CB-1 — DWG-02/}));
  const crossPage = screen.getByRole('region',{name:'선택 연결 도면 근거'});
  expect(within(crossPage).getByText(/다른 쪽과 자동 연결하지 않았습니다/)).toBeTruthy();
  expect(within(crossPage).queryByRole('button',{name:'상대 장비 확인'})).toBeNull();
  expect(fetchAsset).toHaveBeenCalledTimes(1);
});

it.each(['version','document'])('다른 %s 응답에서는 원본 요청과 연결 표시를 차단한다', async mismatch => {
  response = {...response, [mismatch === 'version' ? 'document_version_id' : 'document_id']:'wrong'};
  show(); open(); await screen.findByRole('alert');
  expect(fetchAsset).not.toHaveBeenCalled(); expect(screen.queryByRole('img')).toBeNull();
});

it.each([
  ['NOT_ANALYZED','이 판본에는 단선결선도 분석 기록이 없습니다.'],
  ['ANALYZED','이 판본에서 저장된 단선결선도 연결 후보가 없습니다.'],
  ['PARTIAL','도면 분석 일부를 확인하지 못했습니다.'],
] as const)('빈 결과의 %s 상태를 구분한다', async (status,message) => {
  response = {...response,items:[],processing_status:status}; show(); open();
  await screen.findByText(text => text.startsWith(message)); expect(fetchAsset).not.toHaveBeenCalled();
});

it('조회 오류를 분석 없음으로 바꾸지 않는다', async () => {
  vi.mocked(listDocumentDiagrams).mockRejectedValue(new Error('도면 조회 실패'));
  show(); open(); await screen.findByText('도면 조회 실패');
  expect(screen.queryByText(/이 판본에는 단선결선도 분석 기록이 없습니다/)).toBeNull();
});

it('PDF는 검증된 단일 페이지 바이트로 canvas에 표시하고 다른 페이지는 허용하지 않는다', async () => {
  item.mime = 'application/pdf'; item.page = 7; fetchAsset.mockImplementation(async () => assetResponse('application/pdf'));
  const {container} = show(); open();
  await waitFor(() => expect(pdf.render).toHaveBeenCalledTimes(1));
  expect(pdf.getDocument).toHaveBeenCalledWith(expect.objectContaining({enableXfa:false,useSystemFonts:true}));
  expect(new Uint8Array(pdf.getDocument.mock.calls[0][0].data)).toEqual(bytes);
  expect(container.querySelector('iframe')).toBeNull();
  expect(screen.getByLabelText('보존된 PDF 도면 원문 페이지')).toBeTruthy();
  cleanup();
  pdf.getDocument.mockReturnValue({promise:Promise.resolve({numPages:2}),destroy:pdf.destroy});
  show(); open(); await screen.findByText('선택 도면의 단일 원문 페이지를 확인할 수 없습니다.');
  expect(screen.getByRole('link',{name:'도면 원본 내려받기'}).getAttribute('href')).toBe('blob:original-diagram');
  expect(screen.queryByLabelText('AI 추정 위치 · 연결 확정 아님')).toBeNull();
});

it('닫으면 진행 중 원본 요청을 취소하고 완료된 원본 URL을 해제한다', async () => {
  const view = show(); open(); await screen.findByRole('img');
  const signal = fetchAsset.mock.calls[0][1].signal as AbortSignal;
  view.unmount(); expect(signal.aborted).toBe(true); expect(revokeUrl).toHaveBeenCalledWith('blob:original-diagram');
});

it('다른 판본으로 전환하면 이전 후보·원본을 버린다', async () => {
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  const ui = (doc:DocumentVersion) => <QueryClientProvider client={client}><MemoryRouter><DiagramReview document={doc} projectId="project1" actorId="actor1" /></MemoryRouter></QueryClientProvider>;
  const view = render(ui(document)); open(); await screen.findByRole('img');
  response = {...response,document_version_id:'version2',items:[],processing_status:'NOT_ANALYZED'};
  view.rerender(ui({...document,document_version_id:'version2',version:3}));
  await screen.findByText(/이 판본에는 단선결선도 분석 기록이 없습니다/);
  expect(screen.queryByRole('img')).toBeNull(); expect(screen.queryByText('수전 변압기')).toBeNull();
  expect(revokeUrl).toHaveBeenCalledWith('blob:original-diagram');
});

it('보존 바이트를 반환하고 무결성 오류·권한 오류는 표시 전에 거부한다', async () => {
  expect(new Uint8Array(await readDiagramAsset(document,item,new AbortController().signal))).toEqual(bytes);
  fetchAsset.mockImplementationOnce(async () => assetResponse('image/jpeg'));
  await expect(readDiagramAsset(document,item,new AbortController().signal)).rejects.toThrow('형식');
  await expect(readDiagramAsset(document,{...item,size:9},new AbortController().signal)).rejects.toThrow('크기');
  await expect(readDiagramAsset(document,{...item,sha256:'0'.repeat(64)},new AbortController().signal)).rejects.toThrow('일치하지');
  fetchAsset.mockResolvedValueOnce(new Response(null,{status:403}));
  await expect(readDiagramAsset(document,item,new AbortController().signal)).rejects.toMatchObject({status:403});
  const prior = fetchAsset.mock.calls.length;
  await expect(readDiagramAsset(document,{...item,mime:'image/svg+xml'} as unknown as DiagramItem,new AbortController().signal)).rejects.toThrow('메타데이터');
  await expect(readDiagramAsset(document,{...item,size:8*1024*1024+1},new AbortController().signal)).rejects.toThrow('메타데이터');
  expect(fetchAsset.mock.calls.length).toBe(prior);
});

it('API 계약의 생략된 빈 목록 기본값을 추정 연결 없이 표시한다', async () => {
  item.graph = {diagram_type:'UNKNOWN',title:'도면 유형 미확인',review_required:true};
  show(); open(); await screen.findByRole('img');
  expect(screen.getByRole('heading',{name:'장비·기호 후보 0개'})).toBeTruthy();
  expect(screen.getByRole('heading',{name:'연결 후보 0개'})).toBeTruthy();
  expect(screen.getByText('저장된 연결 후보가 없습니다. 연결이 없다는 확정은 아닙니다.')).toBeTruthy();
});


it('DRAWING은 바로 열리고 저장된 도면·기호·연결 후보 집계를 요구 수와 구분한다', async () => {
  response = {...response,processing_status:'PARTIAL',warnings:['교차선 확인 필요']};
  show({...document,kind:'DRAWING'});
  await screen.findByRole('img');
  expect(screen.getByText('단선결선도 연결 검토').closest('details')?.open).toBe(true);
  const summary = screen.getByRole('region',{name:'도면 분석 결과 요약'});
  expect(summary.textContent).toContain('도면 원본 1개');
  expect(summary.textContent).toContain('장비·기호 후보 2개');
  expect(summary.textContent).toContain('연결 후보 2개');
  expect(summary.textContent).toContain('확인 필요');
});

it('DRAWING 조회 중에는 완료 수치를 표시하지 않는다', async () => {
  vi.mocked(listDocumentDiagrams).mockReturnValue(new Promise(() => undefined));
  show({...document,kind:'DRAWING'});
  await screen.findByText('현재 판본의 도면 분석 읽는 중');
  expect(screen.queryByRole('region',{name:'도면 분석 결과 요약'})).toBeNull();
});


it('중복 원문은 검증된 도면이 실제 표시된 뒤에만 숨기고 표시 실패·종료 시 복원한다', async () => {
  const visible=vi.fn();
  const view=show({...document,kind:'DRAWING'},visible);
  const image=await screen.findByRole('img');
  expect(visible).not.toHaveBeenCalledWith(true);
  fireEvent.load(image);
  await waitFor(()=>expect(visible).toHaveBeenLastCalledWith(true));
  fireEvent.error(image);
  await waitFor(()=>expect(visible).toHaveBeenLastCalledWith(false));
  expect(screen.getByRole('link',{name:'도면 원본 내려받기'})).toBeTruthy();
  view.unmount();expect(visible).toHaveBeenLastCalledWith(false);
});
