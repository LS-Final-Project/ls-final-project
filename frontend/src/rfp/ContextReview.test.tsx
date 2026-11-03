// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ContextReview } from './ContextReview';
import { CONTEXT_CATEGORIES, getContext, processContext, updateContext, type ContextEnvelope } from '../api/rfp-context';
import { ApiError } from '../api/client';
import type { DocumentVersion } from '../api/rfp';
vi.mock('../api/rfp-context',async original=>({...await original<object>(),getContext:vi.fn(),processContext:vi.fn(),updateContext:vi.fn()}));
const documents=[{document_id:'doc1',document_version_id:'v1',title:'첫 문서',version:1},{document_id:'doc2',document_version_id:'v2',title:'둘째 문서',version:2}] as DocumentVersion[];
const fixture=():ContextEnvelope=>({project_id:'p',version:3,status:'CURRENT',source_fingerprint:'a'.repeat(64),current_source_fingerprint:'a'.repeat(64),provenance:'ai_draft',created_at:null,created_by:null,reason:null,requirements:[],source_refs:Object.keys(CONTEXT_CATEGORIES).map((_,i)=>({requirement_id:`r${i}`,version:1,code:`R-${i}`,document_id:i===1?'doc2':'doc1',document_version_id:i===1?'v2':'v1',clause_id:`c${i}`,page:1,quote:`합성 인용 ${i}`})),result:{schema_version:'rfp-context-v1',prompt_version:'rfp-context-v1',outcome:'PARTIAL',groups:[{entity_id:'system',name:'전력계통',kind:'SYSTEM',evidence_requirement_ids:['r0'],diagram_source_ids:[],needs_review:true},{entity_id:'equipment',name:'변압기',kind:'EQUIPMENT',parent_id:'system',evidence_requirement_ids:['r0'],diagram_source_ids:['d1'],needs_review:true}],assignments:Object.keys(CONTEXT_CATEGORIES).map((category,i)=>({requirement_id:`r${i}`,primary_category:category as keyof typeof CONTEXT_CATEGORIES,additional_categories:i===0?['QUALITY']:[],group_ids:['system','equipment'],relations:[{group_id:'system',relation:'APPLIES_TO'},{group_id:'equipment',relation:'RELATED'}],reason:`근거 ${i}`,evidence_requirement_ids:[`r${i}`],needs_review:true})),issues:[{code:'MISSING',requirement_ids:['r8'],entity_ids:[],message:'미분류 요구 확인 필요'}]}});
const started=vi.fn(), source=vi.fn();
function show(){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})}><ContextReview projectId="p" actorId="actor" document={documents[0]} documents={documents} onStarted={started} onSource={source}/></QueryClientProvider>);}
beforeEach(()=>{vi.mocked(getContext).mockResolvedValue({data:fixture()});vi.mocked(processContext).mockResolvedValue({data:{job_id:'job',status:'QUEUED',status_url:'/jobs/job'}});});
afterEach(()=>{cleanup();vi.clearAllMocks();});
it('전체 맥락을 선택 문서로 구분하고 공통 연결 요구를 중복 집계하지 않는다',async()=>{
 show();await screen.findByText(/고유 요구 8건/);expect(processContext).not.toHaveBeenCalled();
 expect(screen.getByRole('button',{name:/전력계통 › 변압기/})).toBeTruthy();
 fireEvent.change(screen.getByLabelText('표시 범위'),{target:{value:'project'}});expect(screen.getByText(/고유 요구 9건/)).toBeTruthy();
 fireEvent.click(screen.getByRole('tab',{name:'8개 분류'}));
 for(const name of Object.values(CONTEXT_CATEGORIES))expect(screen.getByRole('button',{name:new RegExp(`^${name} `)})).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'품질·시험 2'}));expect(screen.getByText('R-0 · 설비·장비')).toBeTruthy();expect(screen.getByText('R-4 · 품질·시험')).toBeTruthy();
});
it('근거 원문 이동은 정확한 문서·요구 판본 정보를 넘긴다',async()=>{
 show();await screen.findByText(/고유 요구 8건/);fireEvent.change(screen.getByLabelText('표시 범위'),{target:{value:'project'}});
 fireEvent.click(screen.getByRole('button',{name:'R-1 원문 · 문서 이동'}));expect(source).toHaveBeenCalledWith(expect.objectContaining({requirement_id:'r1',version:1,document_version_id:'v2',document_id:'doc2'}));
});
it('아직 미실행과 오래된 결과를 구분하며 stale 결과는 수정하지 않는다',async()=>{
 const stale={...fixture(),status:'STALE' as const};vi.mocked(getContext).mockResolvedValue({data:stale});show();await screen.findByRole('alert');expect(screen.getByRole('alert').textContent).toContain('이전 결과');for(const b of screen.getAllByRole('button',{name:'분류·묶음 수정'}))expect((b as HTMLButtonElement).disabled).toBe(true);expect(updateContext).not.toHaveBeenCalled();
});
it('미실행 결과는 GET만으로 처리하지 않고 명시 클릭 1회만 접수한다',async()=>{
 vi.mocked(getContext).mockResolvedValue({data:{...fixture(),version:null,status:'NOT_ANALYZED',result:null}});show();await screen.findByText(/아직 맥락별 정리를 실행하지 않았습니다/);expect(processContext).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'맥락별 정리'}));await waitFor(()=>expect(started).toHaveBeenCalledWith(expect.objectContaining({job_id:'job'})));expect(processContext).toHaveBeenCalledTimes(1);
});
it('사람 수정은 원판본·fingerprint와 전체 결과를 새 판본 PATCH로 보낸다',async()=>{
 const valid=fixture();valid.result!.outcome='VALID';valid.result!.assignments=valid.result!.assignments.map(a=>({...a,needs_review:false}));vi.mocked(getContext).mockResolvedValue({data:valid});
 vi.mocked(updateContext).mockResolvedValue({data:{...fixture(),version:4,provenance:'human_input',reason:'사람이 확인한 분류'}});show();await screen.findByText(/고유 요구 8건/);
 fireEvent.click(screen.getAllByRole('button',{name:'분류·묶음 수정'})[0]);fireEvent.change(screen.getByLabelText('주 분류'),{target:{value:'QUALITY'}});fireEvent.change(screen.getByLabelText('수정 사유'),{target:{value:'사람이 확인한 분류'}});fireEvent.click(screen.getByRole('button',{name:'새 판본으로 저장'}));
 await screen.findByText(/맥락 판본 4 · 사람 수정/);expect(updateContext).toHaveBeenCalledWith('p',expect.objectContaining({expected_version:3,expected_source_fingerprint:'a'.repeat(64),reason:'사람이 확인한 분류',result:expect.objectContaining({outcome:'PARTIAL',assignments:expect.arrayContaining([expect.objectContaining({requirement_id:'r0',primary_category:'QUALITY',additional_categories:[],needs_review:true})])})}),expect.any(String));
});
it('PATCH 충돌은 실패를 숨기거나 자동 재처리하지 않는다',async()=>{
 vi.mocked(updateContext).mockRejectedValue(new ApiError(409,'conflict'));show();await screen.findByText(/고유 요구 8건/);fireEvent.click(screen.getAllByRole('button',{name:'분류·묶음 수정'})[0]);fireEvent.change(screen.getByLabelText('수정 사유'),{target:{value:'수정'}});fireEvent.click(screen.getByRole('button',{name:'새 판본으로 저장'}));await screen.findByText(/다른 수정이나 원문 변경/);expect(processContext).not.toHaveBeenCalled();expect(updateContext).toHaveBeenCalledTimes(1);
});
it('GET 오류는 빈 결과로 숨기지 않는다',async()=>{vi.mocked(getContext).mockRejectedValue(new ApiError(403,'접근 거부'));show();await screen.findByRole('alert');expect(screen.queryByText(/아직 맥락별/)).toBeNull();expect(processContext).not.toHaveBeenCalled();});

it('맥락 요구는 20개씩 번호로 이동하고 묶음·분류·범위 변경은 첫 페이지로 돌아간다',async()=>{
 const data=fixture(), original=data.result!.assignments[0], ref=data.source_refs[0];
 data.result!.assignments=Array.from({length:45},(_,i)=>({...original,requirement_id:`many${i}`,evidence_requirement_ids:[]}));
 data.source_refs=data.result!.assignments.map((a,i)=>({...ref,requirement_id:a.requirement_id,code:`M-${i}`}));
 vi.mocked(getContext).mockResolvedValue({data});show();await screen.findByText(/고유 요구 45건/);
 expect(screen.getAllByRole('button',{name:'분류·묶음 수정'})).toHaveLength(20);expect(screen.queryByText('M-20 · 설비·장비')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'2페이지'}));expect(screen.queryByText('M-0 · 설비·장비')).toBeNull();expect(screen.getByText('M-20 · 설비·장비')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'M-20 원문'}));expect(source).toHaveBeenCalledWith(expect.objectContaining({requirement_id:'many20',document_version_id:'v1'}));
 fireEvent.click(screen.getByRole('button',{name:'3페이지'}));expect(screen.getAllByRole('button',{name:'분류·묶음 수정'})).toHaveLength(5);
 fireEvent.click(screen.getByRole('button',{name:/전력계통 › 변압기/}));expect(screen.getByRole('button',{name:'1페이지'}).getAttribute('aria-current')).toBe('page');
 fireEvent.click(screen.getByRole('button',{name:'2페이지'}));fireEvent.click(screen.getByRole('tab',{name:'8개 분류'}));expect(screen.getByText('M-0 · 설비·장비')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'2페이지'}));fireEvent.change(screen.getByLabelText('표시 범위'),{target:{value:'project'}});expect(screen.getByRole('button',{name:'1페이지'}).getAttribute('aria-current')).toBe('page');
 expect(processContext).not.toHaveBeenCalled();
});
