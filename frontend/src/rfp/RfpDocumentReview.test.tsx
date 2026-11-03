// @vitest-environment jsdom
import { QueryClient,QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { cleanup,fireEvent,render,screen,waitFor,within } from '@testing-library/react';
import { afterEach,beforeEach,expect,it,vi } from 'vitest';
import { RfpDocumentReview } from './RfpDocumentReview';
import { MOCK_DETAIL,MOCK_REQUIREMENTS } from '../mocks/rfp-table';
import { getDocument,listDocumentDiagrams,listRequirements,type DocumentVersion } from '../api/rfp';
import { getSupplementRequirement } from '../api/rfp-supplement';
import { getContext,processContext,type ContextEnvelope } from '../api/rfp-context';
import { getProject } from '../api/tft';
vi.mock('../api/rfp',async original=>({...await original<object>(),getDocument:vi.fn(),listDocumentDiagrams:vi.fn(),listRequirements:vi.fn()}));
vi.mock('../api/rfp-supplement',async original=>({...await original<object>(),getSupplementRequirement:vi.fn()}));
vi.mock('../api/rfp-context',async original=>({...await original<object>(),getContext:vi.fn(),processContext:vi.fn()}));
vi.mock('../api/tft',()=>({getProject:vi.fn()}));
vi.mock('./OriginalDocument',()=>({OriginalDocument:({document,origin}:{document:DocumentVersion;origin?:{quote:string}})=><div data-testid="original">{document.title}<span data-testid="quote">{origin?.quote}</span></div>}));
vi.mock('./ResponseDecision',()=>({ResponseDecision:({requirement,canSelect,reviewDepartments}:{requirement:{requirement_id:string};canSelect:boolean;reviewDepartments?:string[]})=><div data-testid="decision">{requirement.requirement_id}<span>{canSelect?'저장 가능':'읽기 전용'}</span><span>{reviewDepartments?.join(',')}</span></div>}));
const docs=['first','second'].map((title,i)=>({title,document_id:`doc${i}`,document_version_id:`v${i}`,kind:'RFP_SPEC',version:1})) as DocumentVersion[];
const rows=MOCK_REQUIREMENTS.slice(0,2) as never;
const show=(documents=docs,busy=false)=>render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter><RfpDocumentReview projectId="p" actorId="a" documents={documents} busy={busy}/></MemoryRouter></QueryClientProvider>);
async function chooseFirst(){fireEvent.click(await screen.findByRole('button',{name:/미정리 요구/}));fireEvent.click(screen.getByRole('button',{name:/R-01/}));}
beforeEach(()=>{
 vi.mocked(getProject).mockResolvedValue({data:{id:'p',name:'프로젝트',sales_user_id:'a',responsible_user_id:null,version:2,review_departments:['RND_FW']} as never,meta:{request_id:'test'}});
 vi.mocked(getDocument).mockResolvedValue({data:{document_version_id:'v0',extraction_warnings:[]} as never});
 vi.mocked(getContext).mockResolvedValue({data:{project_id:'p',status:'NOT_ANALYZED',result:null,source_refs:[],requirements:[]} as unknown as ContextEnvelope});
 vi.mocked(listRequirements).mockImplementation(async(_p,filters)=>({data:{items:filters.document_version_id==='v1'?[]:rows,next_cursor:null}}));
 vi.mocked(getSupplementRequirement).mockImplementation(async id=>({data:{...MOCK_DETAIL,requirement_id:id,origin:{...MOCK_DETAIL.origin,document_version_id:'v0',quote:id.endsWith('1')?'first quote':'second quote'}} as never}));
});
afterEach(()=>{cleanup();vi.clearAllMocks();});
it('has one review entry and connects target → customer requirement → real response workflow',async()=>{
 show();await screen.findByRole('heading',{name:'RFP 검토'});expect(screen.queryByText('기존 요구·대응 검토')).toBeNull();expect(screen.queryByText('맥락별 검토')).toBeNull();expect(screen.queryByTestId('decision')).toBeNull();
 await chooseFirst();await waitFor(()=>expect(screen.getByTestId('quote').textContent).toBe('first quote'));expect(screen.getByTestId('decision').textContent).toContain('저장 가능');expect(screen.getByTestId('decision').textContent).toContain('RND_FW');
 fireEvent.click(screen.getByRole('button',{name:/R-02/}));await waitFor(()=>expect(screen.getByTestId('quote').textContent).toBe('second quote'));
 fireEvent.change(screen.getByLabelText('검토 문서'),{target:{value:'v1'}});await screen.findByText('이 범위에 추출된 고객 요구가 없습니다.');expect(screen.getByTestId('quote').textContent).toBe('');expect(screen.queryByTestId('decision')).toBeNull();expect(listRequirements).toHaveBeenLastCalledWith('p',{document_version_id:'v1'},100,undefined,{signal:expect.any(AbortSignal),useMockFallback:false});expect(processContext).not.toHaveBeenCalled();
});
it.each(['document','requirement-version','canonical-version','detail-error'] as const)('never highlights or mounts response when %s does not match',async mismatch=>{
 if(mismatch==='canonical-version')vi.mocked(listRequirements).mockResolvedValue({data:{items:[{...MOCK_REQUIREMENTS[0],version:3}] as never,next_cursor:null}});
 if(mismatch==='detail-error')vi.mocked(getSupplementRequirement).mockRejectedValue(new Error('접근 거절'));
 else vi.mocked(getSupplementRequirement).mockResolvedValue({data:{...MOCK_DETAIL,version:mismatch==='requirement-version'?2:1,origin:{...MOCK_DETAIL.origin,document_version_id:mismatch==='document'?'other':'v0'}} as never});
 show();await chooseFirst();await screen.findByText(mismatch==='detail-error'?'접근 거절':'요구 또는 문서 판본이 일치하지 않아 원문 강조와 대응 선택을 보류했습니다.');expect(screen.getByTestId('quote').textContent).toBe('');expect(screen.queryByTestId('decision')).toBeNull();
});
it('loads all summary cursors and clears the old selected response on page or target changes',async()=>{
 const many=Array.from({length:12},(_,i)=>({...MOCK_REQUIREMENTS[0],requirement_id:`r${i}`,code:`R-${String(i+1).padStart(2,'0')}`}));vi.mocked(listRequirements).mockImplementation(async(_p,_filters,_limit,cursor)=>({data:{items:(cursor?many.slice(10):many.slice(0,10)) as never,next_cursor:cursor?null:'next'}}));
 show();fireEvent.click(await screen.findByRole('button',{name:/미정리 요구/}));expect(listRequirements).toHaveBeenLastCalledWith('p',{document_version_id:'v0'},100,'next',{signal:expect.any(AbortSignal),useMockFallback:false});fireEvent.click(screen.getByRole('button',{name:/R-01/}));await screen.findByTestId('decision');fireEvent.click(screen.getByRole('button',{name:'2페이지'}));expect(screen.queryByTestId('decision')).toBeNull();expect(screen.getByTestId('quote').textContent).toBe('');expect(within(screen.getByRole('region',{name:'선택 대상 고객 요구'})).getAllByRole('button',{name:/R-/})).toHaveLength(2);
});
it.each(['busy','not-owner','project-error'] as const)('keeps response read-only for %s',async reason=>{
 if(reason==='not-owner')vi.mocked(getProject).mockResolvedValue({data:{sales_user_id:'other',responsible_user_id:'other',version:2,review_departments:[]} as never,meta:{request_id:'test'}});
 if(reason==='project-error')vi.mocked(getProject).mockRejectedValue(new Error('프로젝트 접근 거절'));
 show(docs,reason==='busy');await chooseFirst();await waitFor(()=>expect(screen.getByTestId('decision').textContent).toContain('읽기 전용'));
});
it.each(['warnings','wrong-version','error'] as const)('preserves source and actual requirements while extraction info is %s',async state=>{
 if(state==='warnings')vi.mocked(getDocument).mockResolvedValue({data:{document_version_id:'v0',extraction_warnings:['PDF 원문 2쪽: 이미지 전사 불확실']} as never});
 if(state==='wrong-version')vi.mocked(getDocument).mockResolvedValue({data:{document_version_id:'old',extraction_warnings:['다른 판본 경고']} as never});
 if(state==='error')vi.mocked(getDocument).mockRejectedValue(new Error('문서 조회 실패'));
 show();await screen.findByText(state==='warnings'?'PDF 원문 2쪽: 이미지 전사 불확실':'현재 판본 추출 안내 확인 불가');await chooseFirst();await screen.findByTestId('decision');expect(screen.getByTestId('original').textContent).toContain('first');expect(screen.queryByText('다른 판본 경고')).toBeNull();
});
it.each(['empty','error','wrong-version'] as const)('preserves zero-text drawing original and diagram inspection for %s',async state=>{
 vi.mocked(listRequirements).mockResolvedValue({data:{items:[],next_cursor:null}});
 if(state==='error')vi.mocked(listDocumentDiagrams).mockRejectedValue(new Error('도면 조회 실패'));else vi.mocked(listDocumentDiagrams).mockResolvedValue({data:{document_id:'doc0',document_version_id:state==='wrong-version'?'old':'v0',processing_status:'NOT_ANALYZED',items:[],warnings:[]}});
 show([{...docs[0],kind:'DRAWING'}]);await screen.findByText('추출된 문자 요구는 0건입니다. 도면의 장비·기호와 연결 후보는 위 도면 검토에서 확인하세요.');expect(screen.getByTestId('original')).toBeTruthy();expect(screen.getByRole('region',{name:'RFP 검토'}).id).toBe('rfp-document-review');expect(screen.queryByTestId('decision')).toBeNull();
});

it('a cross-document source v2 cannot highlight when the actual destination requirement is v3 even if cached detail is v2',async()=>{
 const first=MOCK_REQUIREMENTS[0],other={...MOCK_REQUIREMENTS[1],version:3};
 vi.mocked(listRequirements).mockImplementation(async(_p,filters)=>({data:{items:filters.document_version_id==='v1'?[other] as never:[first] as never,next_cursor:null}}));
 vi.mocked(getContext).mockResolvedValue({data:{project_id:'p',version:1,status:'CURRENT',source_fingerprint:'a',current_source_fingerprint:'a',requirements:[],source_refs:[{requirement_id:first.requirement_id,version:1,code:first.code,document_id:'doc0',document_version_id:'v0',page:1,quote:'first',clause_id:null},{requirement_id:other.requirement_id,version:2,code:other.code,document_id:'doc1',document_version_id:'v1',page:1,quote:'old other',clause_id:null}],result:{groups:[{entity_id:'target',name:'변압기',kind:'EQUIPMENT',needs_review:false,evidence_requirement_ids:[],diagram_source_ids:[]}],assignments:[{requirement_id:first.requirement_id,primary_category:'EQUIPMENT',additional_categories:[],group_ids:['target'],relations:[],evidence_requirement_ids:[other.requirement_id],reason:'원문',needs_review:false}],issues:[]}} as unknown as ContextEnvelope});
 vi.mocked(getSupplementRequirement).mockImplementation(async id=>({data:{...MOCK_DETAIL,requirement_id:id,version:id===other.requirement_id?2:1,origin:{...MOCK_DETAIL.origin,document_version_id:id===other.requirement_id?'v1':'v0',quote:'stale cached quote'}} as never}));
 show();fireEvent.click(await screen.findByRole('button',{name:/변압기.*설비/}));fireEvent.click(screen.getByRole('button',{name:/R-01/}));await screen.findByTestId('decision');fireEvent.click(screen.getByRole('button',{name:'R-02 원문 · 문서 이동'}));await screen.findByText('요구 또는 문서 판본이 일치하지 않아 원문 강조와 대응 선택을 보류했습니다.');expect(screen.getByTestId('original').textContent).toContain('second');expect(screen.getByTestId('quote').textContent).toBe('');expect(screen.queryByTestId('decision')).toBeNull();
});
