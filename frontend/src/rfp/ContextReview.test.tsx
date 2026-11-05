// @vitest-environment jsdom
import {useState} from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ContextReview } from './ContextReview';
import { getContext, processContext, updateContext, type ContextEnvelope } from '../api/rfp-context';
import { ApiError } from '../api/client';
import { listRequirements, type DocumentVersion, type RequirementSummary } from '../api/rfp';
import { MOCK_REQUIREMENTS } from '../mocks/rfp-table';
vi.mock('../api/rfp-context',async original=>({...await original<object>(),getContext:vi.fn(),processContext:vi.fn(),updateContext:vi.fn()}));
vi.mock('../api/rfp',async original=>({...await original<object>(),listRequirements:vi.fn()}));
const documents=[{document_id:'doc1',document_version_id:'v1',title:'첫 문서',version:1},{document_id:'doc2',document_version_id:'v2',title:'둘째 문서',version:2}] as DocumentVersion[];
const rows=Array.from({length:12},(_,i)=>({...MOCK_REQUIREMENTS[0],requirement_id:`r${i}`,code:`1234abcd:2.1:${i+1}`})) as RequirementSummary[];
const fixture=():ContextEnvelope=>({project_id:'p',version:3,status:'CURRENT',source_fingerprint:'a'.repeat(64),current_source_fingerprint:'a'.repeat(64),provenance:'ai_draft',created_at:null,created_by:null,reason:null,requirements:[],source_refs:rows.map((row,i)=>({requirement_id:row.requirement_id,version:1,code:row.code,document_id:'doc1',document_version_id:'v1',clause_id:`c${i}`,page:1,quote:`합성 인용 ${i}`})),result:{schema_version:'rfp-context-v1',prompt_version:'rfp-context-v1',outcome:'PARTIAL',groups:[{entity_id:'system',name:'전력계통',kind:'SYSTEM',evidence_requirement_ids:['r0'],diagram_source_ids:[],needs_review:true},{entity_id:'equipment',name:'변압기',tag:'TR-01',site:'동관',kind:'EQUIPMENT',parent_id:'system',evidence_requirement_ids:['r0'],diagram_source_ids:['d1'],needs_review:true}],assignments:rows.map((row,i)=>({requirement_id:row.requirement_id,primary_category:i===1?'QUALITY':'EQUIPMENT',additional_categories:i===0?['QUALITY']:[],group_ids:['equipment'],relations:[],reason:`근거 ${i}`,evidence_requirement_ids:[row.requirement_id],needs_review:true})),issues:[]}});
const started=vi.fn(),source=vi.fn(),selected=vi.fn(),cleared=vi.fn();
function Harness({items=rows}:{items?:RequirementSummary[]}){const [id,setId]=useState<string>();return <ContextReview projectId="p" actorId="actor" document={documents[0]} documents={documents} requirements={items} selectedRequirementId={id} onStarted={started} onSource={source} onSelectRequirement={(row,ref)=>{setId(row.requirement_id);selected(row,ref);}} onClearSelection={()=>{setId(undefined);cleared();}}/>;}
function show(items=rows){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})}><Harness items={items}/></QueryClientProvider>);}
async function choose(){fireEvent.click(await screen.findByRole('button',{name:/변압기.*전력계통/}));}
beforeEach(()=>{vi.mocked(getContext).mockResolvedValue({data:fixture()});vi.mocked(listRequirements).mockResolvedValue({data:{items:rows,next_cursor:null}});vi.mocked(processContext).mockResolvedValue({data:{job_id:'job',status:'QUEUED',status_url:'/jobs/job'}});});
afterEach(()=>{cleanup();vi.clearAllMocks();});
it('starts with actual targets, then ten concise customer requirements grouped by kind without category tabs',async()=>{
 show();await screen.findByText('고객 요구 12건 / 검토 대상 2개');
 expect(screen.queryByRole('tab')).toBeNull();expect(screen.queryByRole('region',{name:'선택 대상 고객 요구'})).toBeNull();
 await choose();const panel=screen.getByRole('region',{name:'선택 대상 고객 요구'});
 const targetToggle=screen.getByText('다른 대상 선택').closest('summary')!;
 expect(targetToggle.textContent).toContain('변압기');expect(targetToggle.textContent).not.toContain('고객 요구');
 expect(panel.querySelector('header')?.textContent).toContain('고객 요구 12건');
 expect(within(panel).getAllByRole('button',{name:/조항 2.1 · 요구/})).toHaveLength(10);
 expect(screen.queryByText(/1234abcd:/)).toBeNull();expect(screen.getByRole('heading',{name:'장비 사양'})).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'2페이지'}));expect(within(panel).getAllByRole('button',{name:/조항 2.1 · 요구/})).toHaveLength(2);
 expect(screen.getByRole('heading',{name:'품질·시험'})).toBeTruthy();expect(cleared).toHaveBeenCalled();expect(processContext).not.toHaveBeenCalled();
});
it('selecting a requirement passes the exact summary/source and reveals only that edit affordance',async()=>{
 show();await choose();fireEvent.click(screen.getByRole('button',{name:/조항 2.1 · 요구 1[^0-9]/}));
 expect((screen.getByText('다른 대상 선택').closest('details') as HTMLDetailsElement).open).toBe(false);expect((screen.getByText('다른 요구 선택').closest('details') as HTMLDetailsElement).open).toBe(false);
 expect(selected).toHaveBeenCalledWith(rows[0],expect.objectContaining({requirement_id:'r0',version:1,document_id:'doc1',document_version_id:'v1'}));
 expect(screen.getAllByRole('button',{name:'요구 종류·대상 연결 수정'})).toHaveLength(1);
 fireEvent.click(screen.getByRole('button',{name:'조항 2.1 · 요구 1 원문'}));expect(source).toHaveBeenCalledWith(expect.objectContaining({requirement_id:'r0',version:1}));
});
it.each(['STALE','NOT_ANALYZED','error','missing-source'] as const)('keeps all actual rows accessible as unorganized after %s',async status=>{
 const data=fixture();if(status==='error')vi.mocked(getContext).mockRejectedValue(new ApiError(403,'접근 거부'));else{if(status==='missing-source')data.source_refs.pop();else data.status=status;vi.mocked(getContext).mockResolvedValue({data});}
 show();fireEvent.click(await screen.findByRole('button',{name:/미정리 요구/}));expect(screen.getAllByRole('button',{name:/조항 2.1 · 요구/})).toHaveLength(10);expect(screen.queryByRole('button',{name:'요구 종류·대상 연결 수정'})).toBeNull();expect(updateContext).not.toHaveBeenCalled();expect(processContext).not.toHaveBeenCalled();
});
it('processing is explicit with visible cost notice and only one POST',async()=>{
 vi.mocked(getContext).mockResolvedValue({data:{...fixture(),status:'NOT_ANALYZED',result:null}});show();await screen.findByText(/아직 검토 대상을 정리하지 않았습니다/);expect(processContext).not.toHaveBeenCalled();expect(screen.getByText('AI 사용료 발생').closest('details')).toBeNull();fireEvent.click(screen.getByRole('button',{name:'검토 대상 정리'}));await waitFor(()=>expect(started).toHaveBeenCalledWith(expect.objectContaining({job_id:'job'})));expect(processContext).toHaveBeenCalledTimes(1);
});
it('human correction keeps expected version/fingerprint and conflict never auto-retries',async()=>{
 vi.mocked(updateContext).mockRejectedValue(new ApiError(409,'conflict'));show();await choose();fireEvent.click(screen.getByRole('button',{name:/조항 2.1 · 요구 1[^0-9]/}));fireEvent.click(screen.getByRole('button',{name:'요구 종류·대상 연결 수정'}));fireEvent.change(screen.getByLabelText('주 요구 종류'),{target:{value:'QUALITY'}});fireEvent.change(screen.getByLabelText('수정 사유'),{target:{value:'원문 시험 요구 확인'}});fireEvent.click(screen.getByRole('button',{name:'새 판본으로 저장'}));await screen.findByText(/다른 수정이나 원문 변경/);expect(updateContext).toHaveBeenCalledWith('p',expect.objectContaining({expected_version:3,expected_source_fingerprint:'a'.repeat(64),result:expect.objectContaining({outcome:'PARTIAL'})}),expect.any(String));expect(updateContext).toHaveBeenCalledTimes(1);
});
it('project source navigation retains exact document identity without leaking internal code',async()=>{
 const data=fixture();data.source_refs[0]={...data.source_refs[0],document_id:'doc2',document_version_id:'v2'};vi.mocked(getContext).mockResolvedValue({data});show(rows.slice(1));await screen.findByText('고객 요구 11건 / 검토 대상 2개');fireEvent.change(screen.getByLabelText('표시 범위'),{target:{value:'project'}});await screen.findByText('고객 요구 12건 / 검토 대상 2개');await choose();fireEvent.click(screen.getByRole('button',{name:/조항 2.1 · 요구 1[^0-9]/}));fireEvent.click(screen.getByRole('button',{name:'조항 2.1 · 요구 1 원문 · 문서 이동'}));expect(source).toHaveBeenCalledWith(expect.objectContaining({document_id:'doc2',document_version_id:'v2',requirement_id:'r0'}));
});
it('target search and six-item pagination keep the chosen customer requirements reachable',async()=>{
 const data=fixture();data.result!.groups=Array.from({length:9},(_,i)=>({...data.result!.groups[1],entity_id:`t${i}`,name:`대상 ${i}`,tag:`TR-${i}`,parent_id:null}));data.result!.assignments.forEach(a=>{a.group_ids=data.result!.groups.map(g=>g.entity_id);});vi.mocked(getContext).mockResolvedValue({data});show();await screen.findByText('고객 요구 12건 / 검토 대상 9개');expect(within(screen.getByRole('group',{name:'검토 대상 선택'})).getAllByRole('button')).toHaveLength(6);fireEvent.click(screen.getByRole('button',{name:'검토 대상 2페이지'}));expect(within(screen.getByRole('group',{name:'검토 대상 선택'})).getAllByRole('button')).toHaveLength(3);fireEvent.change(screen.getByLabelText('검토 대상 찾기'),{target:{value:'TR-8'}});fireEvent.click(screen.getByRole('button',{name:/대상 8/}));expect(screen.getByRole('region',{name:'선택 대상 고객 요구'})).toBeTruthy();
});

it('summarizes unique actual requirements and defaults to unresolved without inferring review departments',async()=>{
 const items=rows.map((row,i)=>({...row,status:i===0?'EVIDENCE_FOUND':row.status,needs_review:false,needs_rereview:false,open_questions:0,rnd_check:'NOT_REQUIRED'})) as RequirementSummary[];
 show(items);await screen.findByText('확인 필요 11건');expect(screen.getByText('근거 있음 1건')).toBeTruthy();
 await choose();const panel=screen.getByRole('region',{name:'선택 대상 고객 요구'});
 expect(within(panel).queryByRole('button',{name:/조항 2.1 · 요구 1[^0-9]/})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'전체 요구'}));
 fireEvent.click(screen.getByRole('button',{name:'2페이지'}));
 expect(within(panel).getByRole('button',{name:/조항 2.1 · 요구 1[^0-9]/})).toBeTruthy();
 expect(screen.getByText('고객 요구 12건 / 검토 대상 2개')).toBeTruthy();
 expect(processContext).not.toHaveBeenCalled();
});
it('an evidenced requirement with a rereview flag still needs confirmation',async()=>{
 const items=rows.map(row=>({...row,status:'EVIDENCE_FOUND',needs_review:false,needs_rereview:false,open_questions:0,rnd_check:'NOT_REQUIRED'})) as RequirementSummary[];
 items[0]={...items[0],needs_rereview:true};show(items);await screen.findByText('확인 필요 1건');await choose();
 expect(screen.getByText('근거 있음 11건')).toBeTruthy();expect(screen.getAllByRole('button',{name:/조항 2.1 · 요구/})).toHaveLength(1);
});
it('all-evidenced targets remain accessible through the all requirements toggle',async()=>{
 const items=rows.map(row=>({...row,status:'EVIDENCE_FOUND',needs_review:false,needs_rereview:false,open_questions:0,rnd_check:'NOT_REQUIRED'})) as RequirementSummary[];
 show(items);await screen.findByText('확인 필요 0건');expect(screen.getByText('근거 있음 12건')).toBeTruthy();
 expect(screen.queryByRole('button',{name:/변압기.*전력계통/})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'전체 요구'}));await choose();
 expect(screen.getByRole('region',{name:'선택 대상 고객 요구'})).toBeTruthy();expect(processContext).not.toHaveBeenCalled();
});

it('counts actual open questions and pending R&D as unresolved even when evidence exists',async()=>{
 const items=rows.map(row=>({...row,status:'EVIDENCE_FOUND',needs_review:false,needs_rereview:false,open_questions:0,rnd_check:'NOT_REQUIRED'})) as RequirementSummary[];
 items[0]={...items[0],open_questions:1};items[1]={...items[1],rnd_check:'PENDING'};
 show(items);await screen.findByText('확인 필요 2건');await choose();
 expect(screen.getAllByRole('button',{name:/조항 2.1 · 요구/})).toHaveLength(2);
 expect(screen.getByText('근거 있음 10건')).toBeTruthy();expect(processContext).not.toHaveBeenCalled();
});
it('filter changes preserve the selected requirement and explain when it is outside the filter',async()=>{
 const items=rows.map(row=>({...row,status:'EVIDENCE_FOUND',needs_review:false,needs_rereview:false,open_questions:0,rnd_check:'NOT_REQUIRED'})) as RequirementSummary[];
 show(items);await screen.findByText('확인 필요 0건');fireEvent.click(screen.getByRole('button',{name:'전체 요구'}));await choose();
 fireEvent.click(screen.getByRole('button',{name:/조항 2.1 · 요구 1[^0-9]/}));
 fireEvent.click(screen.getByRole('button',{name:'확인 필요한 요구'}));
 expect(screen.getByText(/선택한 요구는 현재 필터 밖/)).toBeTruthy();expect(screen.getByText('합성 인용 0')).toBeTruthy();
 expect(selected).toHaveBeenCalledTimes(1);
});

it('mandatory filter uses server membership for both document and project scopes',async()=>{
 vi.mocked(listRequirements).mockImplementation(async(_project,filters)=>({data:{items:filters.unresolved_mandatory?rows.slice(0,1):rows,next_cursor:null}}));
 show();await screen.findByText('확인 필요 12건');
 fireEvent.click(screen.getByRole('button',{name:'확인 남은 필수 요구'}));
 await screen.findByText('필수·조건부 필수 요구 중 확인이 남은 1건입니다.');await choose();
 expect(screen.getAllByRole('button',{name:/조항 2.1 · 요구/})).toHaveLength(1);
 expect(listRequirements).toHaveBeenCalledWith('p',expect.objectContaining({document_version_id:'v1',unresolved_mandatory:true}),expect.any(Number),undefined,expect.anything());
 fireEvent.change(screen.getByLabelText('표시 범위'),{target:{value:'project'}});
 await waitFor(()=>expect(vi.mocked(listRequirements).mock.calls.some(call=>call[1].unresolved_mandatory&&!call[1].document_version_id)).toBe(true));
});
