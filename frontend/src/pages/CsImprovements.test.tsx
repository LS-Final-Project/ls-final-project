import { focusManager } from '@tanstack/react-query';
import { afterEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import CsImprovements from './CsImprovements';
import type { ImprovementDetail } from '../api/cs-improvements';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';
const source=examples.find(x=>x.path==='/api/v1/cs/improvement-requests/dddddddd-dddd-4ddd-8ddd-ddddddddddd1')!.body;
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.unstubAllEnvs();setApiMode('mock');});
// 상세 화면은 역할 확인용 세션 조회(/me)와 근거 비교(case-comparison)·고장 목록(faults)을 함께 부른다. 상세 조회 횟수만 센다.
const detailCalls=(fetch:{mock:{calls:unknown[][]}})=>fetch.mock.calls.filter(([url])=>!String(url).endsWith('/me')&&!String(url).includes('/case-comparison')&&!String(url).includes('/cs/faults')&&!String(url).includes('/ai-draft'));
function view(detail=true){setApiMode('live');return renderAt(detail?'/cs/improvements/test':'/cs/improvements',detail?'/cs/improvements/:requestId':'/cs/improvements',<CsImprovements/>);}
test('근거·조건·회신·조치를 분리하고 확인되지 않은 고객 적용을 추측하지 않는다',async()=>{
 const fetch=vi.fn(async()=>jsonResponse(200,source));vi.stubGlobal('fetch',fetch);view();
 await screen.findByText('수신 부서와 할 일');expect(screen.getByText('부서 조치와 후속 확인')).toBeTruthy();expect(screen.queryByRole('button',{name:'전달'})).toBeNull();expect(detailCalls(fetch)).toHaveLength(1);
});
test('빈 목록은 완료 건수가 아닌 조회 가능한 기록 없음으로 표시한다',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,{data:{items:[]}})));view(false);await screen.findByText('조회 가능한 개선요청이 없습니다.');
});
test.each([401,403,404,503])('오류 %i 뒤 이전 상세를 숨기고 복구한다',async(status)=>{
 let fail=false;vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(fail?status:200,fail?errorBody('FAIL','합성 오류'):source)));view();await screen.findByText('수신 부서와 할 일');fail=true;fireEvent.click(screen.getByText('최신 요청 조회'));await screen.findByRole('alert');expect(screen.queryByText('수신 부서와 할 일')).toBeNull();fail=false;fireEvent.click(screen.getByText('최신 요청 조회'));await screen.findByText('수신 부서와 할 일');
});
test('미배정·조건 미확인·제외 근거를 성공으로 표시하지 않는다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;body.data.request.condition_status='UNKNOWN';body.data.request.receiver_unconfirmed=true;body.data.next_owners=[{actor:'CS_OWNER',user_id:null,dept_code:null,task:'REVIEW'}];body.data.evidence=[{id:'e',ticket_id:'t',ticket_external_id:'SYNTHETIC',case_id:null,case_external_id:null,added_by:null,added_at:'2026-10-27',removed_by:null,removed_at:'2026-10-27',removal_reason:'제품 불일치'}];
 vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,body)));view();await screen.findByText('수신 부서와 할 일');expect(screen.getByText('검토 완료 기록',{selector:'.now b'})).toBeTruthy();expect(screen.getByText('미확인',{selector:'.chip'})).toBeTruthy();
});
test('대기중 반복 클릭을 막고 이탈 시 조회를 취소한다',async()=>{
 let signal:AbortSignal|undefined;const fetch=vi.fn((_url:RequestInfo|URL,init?:RequestInit)=>{signal=init?.signal as AbortSignal;return new Promise<Response>((_r,reject)=>signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));});vi.stubGlobal('fetch',fetch);const page=view();await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));fireEvent.click(screen.getByText('최신 요청 조회'));expect(fetch).toHaveBeenCalledTimes(1);page.unmount();expect(signal?.aborted).toBe(true);
});

test('목록은 실제 응답 제목과 상세 링크를 표시한다',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,{data:{items:[{id:'request-doc',problem_statement:'숨길 문서 요청',request_type:'DOCUMENT',product_code:'TEST',status:'DRAFT',version:1,evidence_count:0,cs_owner:null},{id:'request-one',problem_statement:'합성 문서 검토',request_type:'FAULT',product_code:'TEST',status:'DRAFT',version:1,evidence_count:0,cs_owner:null}]}})));view(false);const link=await screen.findByRole('link',{name:'합성 문서 검토'});expect(link.getAttribute('href')).toBe('/cs/improvements/request-one');expect(screen.queryByText('숨길 문서 요청')).toBeNull();
});

test('재조회 응답을 기다리는 동안 이전 표시를 숨긴다',async()=>{
 let count=0;const fetch=vi.fn((url:RequestInfo|URL,init?:RequestInit)=>{if(String(url).endsWith('/me'))return Promise.resolve(jsonResponse(401,errorBody('SESSION','세션 없음')));if(count++===0)return Promise.resolve(jsonResponse(200,source));return new Promise<Response>((_resolve,reject)=>init?.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));});
 vi.stubGlobal('fetch',fetch);const page=view();await screen.findByText('수신 부서와 할 일');fireEvent.click(screen.getByText('최신 요청 조회'));await waitFor(()=>expect(detailCalls(fetch)).toHaveLength(2));expect(screen.queryByText('수신 부서와 할 일')).toBeNull();page.unmount();
});


// Window focus and manual refresh must not discard an open form or abort its save.
test.each([true])('편집 중 포커스·재조회가 입력과 저장 결과를 보존한다 (detail=%s)',async(detail)=>{
 let release!:()=>void;let saveSignal:AbortSignal|undefined;
 const pending=new Promise<void>(resolve=>{release=resolve;});
 const fetch=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
  if(String(url).endsWith('/me'))return new Response(JSON.stringify({data:{roles:['cs_agent']}}),{headers:{'X-CSRF-Token':'synthetic'}});
  if(init?.method==='POST'||init?.method==='PATCH'){saveSignal=init.signal as AbortSignal;await pending;return jsonResponse(503,errorBody('UNCERTAIN','합성 저장 결과 불확실'));}
  return jsonResponse(200,detail?source:{data:{items:[]}});
 });vi.stubGlobal('fetch',fetch);view(detail);
 fireEvent.click(await screen.findByRole('button',{name:detail?'내용 수정':'새 개선요청 작성'}));
 fireEvent.change(screen.getByLabelText('검토할 문제'),{target:{value:'보존할 합성 입력'}});
 if(!detail){fireEvent.change(screen.getByLabelText('제품 코드'),{target:{value:'SYNTHETIC'}});fireEvent.change(screen.getByLabelText('고장 ID'),{target:{value:'FLT-001'}});fireEvent.click(screen.getByLabelText('TS_LV'));}
 const refresh=screen.getByRole('button',{name:'최신 요청 조회'});
 expect((refresh as HTMLButtonElement).disabled).toBe(true);
 await act(async()=>{focusManager.setFocused(false);focusManager.setFocused(true);});
 expect((screen.getByLabelText('검토할 문제') as HTMLTextAreaElement).value).toBe('보존할 합성 입력');
 fireEvent.click(screen.getByRole('button',{name:detail?'내용 저장':'초안 저장'}));
 await waitFor(()=>expect(saveSignal).toBeTruthy());
 fireEvent.click(refresh);
 await act(async()=>{focusManager.setFocused(false);focusManager.setFocused(true);});
 expect(saveSignal?.aborted).toBe(false);
 await act(async()=>{release();});
 await screen.findByText('합성 저장 결과 불확실');
 expect((screen.getByRole('button',{name:detail?'내용 저장':'초안 저장'}) as HTMLButtonElement).disabled).toBe(true);
 expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST'||init?.method==='PATCH')).toHaveLength(1);
 if(!detail){fireEvent.click(screen.getByRole('button',{name:'입력 버리고 목록 새로 조회'}));await screen.findByRole('button',{name:'새 개선요청 작성'});expect(screen.queryByText('합성 저장 결과 불확실')).toBeNull();expect(fetch.mock.calls.filter(([url,init])=>String(url).endsWith('/improvement-requests')&&!init?.method)).toHaveLength(2);expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1);}
 focusManager.setFocused(undefined);
});

// 운영 빌드(DEV 꺼짐)에서도 업무 UI가 켜져 있고 C/S 역할이면 상세와 처리 동작을 연다. 업무 UI 설정은 모듈을 읽을 때 정해져 새로 불러온다.
test('운영 빌드는 준비 중 대신 실제 상세와 역할별 동작을 보인다',async()=>{
 vi.stubEnv('DEV',false);vi.stubEnv('VITE_BUSINESS_UI_ENABLED','true');vi.resetModules();
 const [{default:Page},{renderAt:render}]=await Promise.all([import('./CsImprovements'),import('../test/render')]);
 const fetch=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:['cs_agent'],dept:null,managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}}):jsonResponse(200,source));
 vi.stubGlobal('fetch',fetch);
 render('/cs/improvements/test','/cs/improvements/:requestId',<Page/>);
 await screen.findByText('수신 부서와 할 일');expect(screen.queryByText(/운영 인증 중계와 권한 검증 후/)).toBeNull();
 expect(await screen.findByRole('button',{name:/^검토 완료 기록/})).toBeTruthy();expect(screen.getByRole('button',{name:'내용 수정'})).toBeTruthy();
});
test('운영 빌드에서 업무 UI가 꺼져 있으면 조회하지 않는다',async()=>{
 vi.stubEnv('DEV',false);vi.stubEnv('VITE_BUSINESS_UI_ENABLED','false');vi.resetModules();
 const [{default:Page},{renderAt:render}]=await Promise.all([import('./CsImprovements'),import('../test/render')]);
 const fetch=vi.fn(async()=>jsonResponse(200,source));vi.stubGlobal('fetch',fetch);
 render('/cs/improvements/test','/cs/improvements/:requestId',<Page/>);
 await screen.findByRole('heading',{name:'개선요청'});expect(fetch).not.toHaveBeenCalled();expect(screen.queryByText('수신 부서와 할 일')).toBeNull();
});

// 결과 불명 뒤 다시 읽기: 최신 상세를 실제로 받으면 처리 동작을 다시 열고, 다시 읽기가 실패하면 막은 채로 둔다.
function uncertainFlow(){
 const state={detail:200,write:503};
 const fetch=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
  if(String(url).endsWith('/me'))return new Response(JSON.stringify({data:{roles:['cs_agent'],dept:null,managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}});
  if(init?.method)return jsonResponse(state.write,errorBody('UNCERTAIN','합성 처리 결과 불확실'));
  return jsonResponse(state.detail,state.detail===200?source:errorBody('FAIL','합성 조회 실패'));
 });vi.stubGlobal('fetch',fetch);view();return {state,fetch};
}
const reviewButton=async()=>(await screen.findByRole('button',{name:/^검토 완료 기록/})) as HTMLButtonElement;
async function becomeUncertain(){
 // 검토 완료는 입력 없이 바로 기록한다(제안 부서는 '내용 수정'에서만 고친다).
 fireEvent.click(await reviewButton());
 // 입력 상자가 없으므로 오류는 '결과 불명' 안내 문장 앞에 붙는다.
 await screen.findByText(/합성 처리 결과 불확실/);
}
test('결과 불명 뒤 최신 상세를 받으면 처리 동작을 다시 시작할 수 있다',async()=>{
 const {fetch}=uncertainFlow();await becomeUncertain();
 fireEvent.click(screen.getByRole('button',{name:'입력 버리고 최신 상태 다시 읽기'}));
 await waitFor(async()=>expect((await reviewButton()).disabled).toBe(false));
 expect(screen.queryByText(/최신 상세를 받기 전에는/)).toBeNull();
 expect(fetch.mock.calls.filter(([,init])=>init?.method)).toHaveLength(1);
});
test('결과 불명 뒤 다시 읽기가 실패하면 막은 채로 두고 이후 성공한 조회에서만 푼다',async()=>{
 const {state}=uncertainFlow();await becomeUncertain();
 state.detail=503;fireEvent.click(screen.getByRole('button',{name:'입력 버리고 최신 상태 다시 읽기'}));
 await screen.findByText('합성 조회 실패');expect(screen.getByText(/최신 상세를 받기 전에는/)).toBeTruthy();
 state.detail=200;fireEvent.click(screen.getByRole('button',{name:'최신 요청 조회'}));
 await waitFor(async()=>expect((await reviewButton()).disabled).toBe(false));expect(screen.queryByText(/최신 상세를 받기 전에는/)).toBeNull();
});
test('내용 수정 중에는 검토 완료를 누를 수 없다',async()=>{
 uncertainFlow();
 fireEvent.click(await screen.findByRole('button',{name:'내용 수정'}));expect((await reviewButton()).disabled).toBe(true);
});

test('근거 비교는 해결 기록 칸을 나란히 두고 다수와 다른 값과 확인할 줄을 표시한다',async()=>{
 const row=(ticket:string,fw:string|null,cause:string,dept:string)=>({row_kind:'EVIDENCE',ticket_id:`${ticket}-0000-4000-8000-000000000000`.slice(0,36),ticket_external_id:ticket,ticket_subject:null,ticket_received_at:'2026-09-01T00:00:00Z',customer_external_id:`C-${ticket}`,product_code:'P',case_id:null,case_external_id:null,symptom:null,root_cause_type:cause,root_cause:null,action_taken:null,resolved_by_dept:dept,known_at:null,fw_version_at_issue:fw,onsite_visit:false});
 const items=[row('T-1','1.72','SETTING','TS_LV'),row('T-2','1.72','SETTING','TS_LV'),row('T-3','1.80','HARDWARE','TS_MV')];
 const fault=structuredClone(source) as unknown as ImprovementDetail;fault.data.request.request_type='FAULT';
 vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).includes('/case-comparison')?jsonResponse(200,{data:{request_id:'test',hidden_case_count:0,items}}):jsonResponse(200,fault)));view();
 // 근거 비교는 항상 접힌 채로 시작한다. 머리를 눌러 편다.
 fireEvent.click(await screen.findByRole('button',{name:/근거 비교/}));
 const table=await screen.findByRole('table',{name:'근거 비교'});
 const rows=[...table.querySelectorAll('tbody tr')];
 expect(rows.map(r=>r.querySelector('td:nth-last-child(2)')!.textContent)).toEqual(['같음','같음','원문 확인']);
 // 원인·FW(묶음을 가르는 조건)는 노랑, 해결 부서(참고)는 회색으로 다르게 표시한다.
 // 원인 유형·해결 부서는 값마다 색(많은 값부터 tone-0), FW는 다수와 다른 값만 노랑.
 expect([...rows[2].querySelectorAll('.cmp.tone')].map(c=>c.className)).toEqual(['cmp tone tone-1','cmp tone tone-1']);
 expect(rows[0].querySelectorAll('.cmp.tone-0')).toHaveLength(2);expect(rows[2].querySelectorAll('.cmp.diff')).toHaveLength(1);
 expect(screen.getByText('판단 필요 2')).toBeTruthy();expect(screen.getByText('참고 1')).toBeTruthy();
});

test('근거 비교에서 후보는 넣고 포함된 근거는 사유를 적어 뺀다',async()=>{
 const base=structuredClone(source) as unknown as ImprovementDetail;base.data.request.request_type='FAULT';
 const ev=base.data.evidence[0];
 const row=(kind:'EVIDENCE'|'CANDIDATE',ticket:string,caseId:string|null,ext:string)=>({row_kind:kind,ticket_id:ticket,ticket_external_id:ext,ticket_subject:null,ticket_received_at:'2026-09-01T00:00:00Z',customer_external_id:'C-1',product_code:'P',case_id:caseId,case_external_id:null,symptom:null,root_cause_type:'SETTING',root_cause:null,action_taken:null,resolved_by_dept:'TS_LV',known_at:null,fw_version_at_issue:null,onsite_visit:null});
 const items=[row('EVIDENCE',ev.ticket_id,ev.case_id,'IN-1'),row('CANDIDATE','cccccccc-cccc-4ccc-8ccc-cccccccccccc','44444444-4444-4444-8444-444444444499','CAND-1')];
 const fetch=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
  const path=String(url);
  if(path.endsWith('/me'))return new Response(JSON.stringify({data:{roles:['cs_agent'],dept:null,managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}});
  if(init?.method)return jsonResponse(200,base);
  if(path.includes('/case-comparison'))return jsonResponse(200,{data:{request_id:base.data.request.id,hidden_case_count:0,items}});
  return jsonResponse(200,base);
 });vi.stubGlobal('fetch',fetch);view();
 // 근거 비교는 항상 접힌 채로 시작한다. 머리를 눌러 편다.
 fireEvent.click(await screen.findByRole('button',{name:/근거 비교/}));
 const table=await screen.findByRole('table',{name:'근거 비교'});
 // FW·현장 방문은 어느 근거에도 값이 없어 칸을 그리지 않는다.
 expect(table.textContent).not.toMatch(/고장 당시 FW|현장 방문/);
 const writes=()=>fetch.mock.calls.filter(([,init])=>init?.method==='POST');
 await waitFor(()=>expect((within(table).getByRole('button',{name:'넣기'}) as HTMLButtonElement).disabled).toBe(false));
 fireEvent.click(within(table).getByRole('button',{name:'넣기'}));
 await waitFor(()=>expect(writes()).toHaveLength(1));
 expect(String(writes()[0][0])).toMatch(/\/evidence$/);expect(JSON.parse(writes()[0][1]!.body as string)).toEqual({ticket_id:'cccccccc-cccc-4ccc-8ccc-cccccccccccc',case_id:'44444444-4444-4444-8444-444444444499',version:base.data.request.version});
 await screen.findByText('CAND-1를 근거에 넣었습니다.');
 fireEvent.click(await screen.findByRole('button',{name:'빼기'}));
 const submit=screen.getByRole('button',{name:'근거에서 빼기'}) as HTMLButtonElement;expect(submit.disabled).toBe(true);
 fireEvent.change(screen.getByLabelText('빼는 사유'),{target:{value:'합성 제외'}});fireEvent.click(submit);
 await waitFor(()=>expect(writes()).toHaveLength(2));
 expect(String(writes()[1][0])).toMatch(new RegExp(`/evidence/${ev.id}/remove$`));expect(JSON.parse(writes()[1][1]!.body as string)).toEqual({reason:'합성 제외',version:base.data.request.version});
});

test('AI 초안은 버튼으로 만들고, 검토 완료를 눌러야 저장과 검토를 기록한다',async()=>{
 const base=structuredClone(source) as unknown as ImprovementDetail;const id=base.data.request.id;
 const draft={id:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',created_at:'2026-11-03T01:00:00Z',request_version:base.data.request.version,stale:false,input_version:'v1',prompt_version:'v1',requested_model:'m',model_version:null,
  problem_statement:'합성 AI 문제',confirmed_facts:[{text:'합성 사실',evidence_refs:['REF-1']}],inconsistencies:[{text:'합성 불일치',evidence_refs:[]}],missing_info:['합성 미확인'],not_claimed:['합성 단정 금지'],review_task:'합성 검토 과제',
  proposed_depts:['TS_LV','NOT_A_DEPT'],dept_questions:[{dept_code:'TS_LV',question:'합성 질문'}],evidence:[{ref:'REF-1',ticket_id:'11111111-1111-4111-8111-111111111111',case_id:null}]};
 let made=false;
 const fetch=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
  const path=String(url);
  if(path.endsWith('/me'))return new Response(JSON.stringify({data:{roles:['cs_agent'],dept:null,managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}});
  if(path.endsWith('/ai-draft')){if(init?.method==='POST')made=true;return jsonResponse(init?.method==='POST'?201:200,{data:{request_id:id,provenance:'ai_draft',draft:made?draft:null,latest_attempt:null}});}
  return jsonResponse(200,base);
 });vi.stubGlobal('fetch',fetch);view();
 const run=await screen.findByRole('button',{name:'✦ AI 초안 작성'}) as HTMLButtonElement;
 await waitFor(()=>expect(run.disabled).toBe(false));fireEvent.click(run);
 await screen.findByRole('heading',{name:'합성 AI 문제'});
 expect(screen.getByText('불일치 정보')).toBeTruthy();expect(screen.getByText('미확인 정보')).toBeTruthy();expect(screen.getByText('합성 질문')).toBeTruthy();
 expect(screen.getByRole('link',{name:'REF-1'}).getAttribute('href')).toBe('/cs/tickets/11111111-1111-4111-8111-111111111111');
 expect(fetch.mock.calls.filter(([,init])=>init?.method==='PATCH')).toHaveLength(0);
 // 반영하지 않은 초안이 있으면 단계 바의 검토 완료 기록은 막고 이유를 보인다.
 await waitFor(()=>expect((screen.getByRole('button',{name:/^검토 완료 기록/}) as HTMLButtonElement).disabled).toBe(true));expect((screen.getByRole('button',{name:/^검토 완료 기록/}) as HTMLButtonElement).title).toMatch(/반영하지 않은 AI 초안이 있습니다/);
 // 수정은 초안 값으로 입력 칸을 연다(저장하지 않음).
 fireEvent.click(screen.getByRole('button',{name:'수정'}));expect((screen.getByLabelText('검토할 문제') as HTMLTextAreaElement).value).toBe('합성 AI 문제');expect((screen.getByLabelText('불일치 정보') as HTMLTextAreaElement).value).toBe('합성 불일치');
 fireEvent.click(screen.getByRole('button',{name:'수정 취소'}));
 fireEvent.click(await screen.findByRole('button',{name:'검토 완료'}));
 await waitFor(()=>expect(fetch.mock.calls.filter(([,init])=>init?.method==='PATCH')).toHaveLength(1));
 const body=JSON.parse(fetch.mock.calls.find(([,init])=>init?.method==='PATCH')![1]!.body as string);
 expect(body).toEqual({version:base.data.request.version,problem_statement:'합성 AI 문제',confirmed_facts:'합성 사실',inconsistencies:'합성 불일치',missing_info:'합성 미확인',not_claimed:'합성 단정 금지',review_task:expect.stringMatching(/^합성 검토 과제\n.+: 합성 질문$/),proposed_depts:['TS_LV']});
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).endsWith('/review')&&init?.method==='POST')).toBe(true));
 const review=fetch.mock.calls.find(([url])=>String(url).endsWith('/review'))!;expect(JSON.parse(review[1]!.body as string)).toEqual({version:base.data.request.version});
});

test('목록은 최근 작성순 10건씩 쪽으로 나누고, 머리를 눌러 정렬하면 첫 쪽으로 간다',async()=>{
 const items=Array.from({length:12},(_,i)=>({id:`r-${i}`,origin:'MANUAL',problem_statement:`합성 요청 ${String(i).padStart(2,'0')}`,request_type:'FAULT',product_code:'TEST',fault_id:null,doc_signal:null,status:'DRAFT',version:1,evidence_count:i,cs_owner:null,created_at:`2026-10-${String(i+1).padStart(2,'0')}T00:00:00Z`}));
 vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,{data:{items}})));view(false);
 const table=await screen.findByRole('table',{name:'개선요청 목록'});
 const titles=()=>[...table.querySelectorAll('tbody tr td:first-child')].map(td=>td.textContent);
 expect(titles()).toHaveLength(10);expect(titles()[0]).toBe('합성 요청 11');
 fireEvent.click(screen.getByRole('button',{name:'2쪽'}));expect(titles()).toEqual(['합성 요청 01','합성 요청 00']);expect(screen.getByText('총 12건 중 12건')).toBeTruthy();
 fireEvent.change(screen.getByLabelText('검색어'),{target:{value:'요청 03'}});expect(titles()).toEqual(['합성 요청 03']);expect(screen.getByText('총 12건 중 1건')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:/조건 지우기/}));expect(titles()).toHaveLength(10);
 fireEvent.click(screen.getByRole('button',{name:/^문제 정렬/}));expect(titles()[0]).toBe('합성 요청 00');expect(titles()).toHaveLength(10);
 fireEvent.click(screen.getByRole('button',{name:/^문제 오름차순/}));expect(titles()[0]).toBe('합성 요청 11');
});

test('규칙 값만 있는 시스템 초안은 칸을 비워(-) 보이고 AI 초안 작성을 안내한다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;const r=body.data.request;
 r.origin='SYSTEM_CANDIDATE';r.request_type='FAULT';r.fault_id='FLT-067';r.problem_statement=`${r.product_code} FLT-067: 90일 안 서로 다른 고객 3/3 도달`;r.confirmed_facts='해결 기록 5건, 고객 5곳, 고객 식별자 없는 기록 0건.';
 vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,body)));view();
 await screen.findByText(/아직 요청 내용이 없습니다/);expect(screen.queryByText(/해결 기록 5건, 고객 5곳/)).toBeNull();expect(screen.getByText('미확인 정보')).toBeTruthy();expect(screen.getAllByText('-',{selector:'.fact-box'}).length).toBe(7);
 cleanup();r.confirmed_facts='사람이 쓴 사실';vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,body)));view();
 await screen.findByText('사람이 쓴 사실');expect(screen.queryByText(/아직 요청 내용이 없습니다/)).toBeNull();
});

test('저장된 AI 초안은 그 판본 그대로일 때 새로 들어와도 보이고, 판본이 바뀌면 현재 내용을 보인다',async()=>{
 const base=structuredClone(source) as unknown as ImprovementDetail;const id=base.data.request.id;
 const draft={id:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab',created_at:'2026-11-03T01:00:00Z',request_version:base.data.request.version,stale:false,input_version:'v1',prompt_version:'v1',requested_model:'m',model_version:null,
  problem_statement:'저장된 AI 문제',confirmed_facts:[],inconsistencies:[],missing_info:[],not_claimed:[],review_task:'',proposed_depts:[],dept_questions:[],evidence:[]};
 const fetch=vi.fn(async(url:RequestInfo|URL,_init?:RequestInit)=>String(url).endsWith('/ai-draft')?jsonResponse(200,{data:{request_id:id,provenance:'ai_draft',draft,latest_attempt:null}}):jsonResponse(200,base));
 vi.stubGlobal('fetch',fetch);view();
 await screen.findByRole('heading',{name:'저장된 AI 문제'});expect(screen.getByRole('button',{name:'검토 완료'})).toBeTruthy();
 expect(fetch.mock.calls.some(([url,init])=>String(url).endsWith('/ai-draft')&&init?.method==='POST')).toBe(false);
 // 반영(또는 직접 수정)으로 판본이 바뀌면 초안 대신 현재 내용을 보인다.
 cleanup();base.data.request.problem_statement='저장된 AI 문제';base.data.request.version+=1;view();
 await screen.findByRole('heading',{name:'저장된 AI 문제'});expect(screen.queryByRole('button',{name:'검토 완료'})).toBeNull();
});

test('처리 상자 없이 부서 동작은 수신 부서 줄에 두고 입력도 그 표 아래에 연다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;const r=body.data.request;
 r.status='SENT';r.version=3;r.current_round=1;body.data.next_owners=[{actor:'DEPARTMENT',user_id:null,dept_code:'TS_TRF',task:'ACKNOWLEDGE'}];
 body.data.departments=[{round:1,dept_code:'TS_TRF',status:'SENT',carried_over:false,sent_version:2,reply_due_at:'2999-01-01T00:00:00Z',rejection_reviewed_at:null,rejection_reviewed_by:null}];
 vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:['dept_staff'],dept:'TS_TRF',managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}}):jsonResponse(200,body)));view();
 await screen.findByText('수신 부서와 할 일');expect(screen.queryByText('검토·전달·부서 처리')).toBeNull();
 // 부서 줄에는 지금 할 차례인 처리 하나만 보인다. 받은 상태에서는 회신(수신 확인은 회신에 합쳤다).
 const next=(await screen.findAllByRole('button',{name:/^회신/})).find(b=>b.closest('td.acts'))!;
 expect([...next.closest('td.acts')!.querySelectorAll('button')].map(b=>b.textContent)).toEqual([next.textContent]);
 expect(screen.queryByRole('button',{name:/^수신 확인/})).toBeNull();
 fireEvent.click(next);
 const form=await screen.findByRole('form',{name:/회신.*입력/});expect(form.closest('#ia-slot-dept')).toBeTruthy();
});

test('검토를 마친 요청은 개선 요청 작성 상자의 부서 전달로 입력 없이 제안 부서에 바로 보낸다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;const r=body.data.request;
 r.status='REVIEWED';r.proposed_depts=['TS_TRF'];body.data.next_owners=[{actor:'CS_OWNER',user_id:null,dept_code:null,task:'SEND'}];
 const fetch=vi.fn(async(url:RequestInfo|URL,_init?:RequestInit)=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:['cs_agent'],dept:null,managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}}):jsonResponse(200,body));
 vi.stubGlobal('fetch',fetch);view();
 const send=await screen.findByRole('button',{name:'부서 전달'});expect(screen.queryByRole('button',{name:/부서 전달 →/})).toBeNull();expect(send.closest('.ir-content')).toBeTruthy();
 await waitFor(()=>expect((send as HTMLButtonElement).disabled).toBe(false));fireEvent.click(send);
 await waitFor(()=>expect(fetch.mock.calls.some(([url])=>String(url).endsWith('/send'))).toBe(true));
 expect(screen.queryByRole('form',{name:'부서 전달 입력'})).toBeNull();
 const call=fetch.mock.calls.find(([url])=>String(url).endsWith('/send'))!;expect(JSON.parse(call[1]!.body as string)).toEqual({version:r.version,dept_codes:['TS_TRF'],reply_due_days:7});
});

test('내용 수정에서 제안 부서를 고르고, 고장 요청은 부서를 비우면 저장하지 않는다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;const r=body.data.request;r.request_type='FAULT';r.fault_id='FLT-001';r.proposed_depts=['TS_TRF'];
 const fetch=vi.fn(async(url:RequestInfo|URL,_init?:RequestInit)=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:['cs_agent'],dept:null,managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}}):jsonResponse(200,body));
 vi.stubGlobal('fetch',fetch);view();
 fireEvent.click(await screen.findByRole('button',{name:'내용 수정'}));
 fireEvent.click(screen.getByRole('button',{name:/ 빼기$/}));
 expect((screen.getByRole('button',{name:'내용 저장'}) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.change(screen.getByLabelText('제안 부서 추가'),{target:{value:'TS_LV'}});expect(screen.getByRole('button',{name:/저압기기 기술지원.* 빼기/})).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'내용 저장'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(true));
 expect(JSON.parse(fetch.mock.calls.find(([,init])=>init?.method==='PATCH')![1]!.body as string)).toEqual({version:r.version,proposed_depts:['TS_LV']});
});

test('단계 바는 지금 해야 할 단계를 표시하고, 근거 칸을 누르면 근거 비교를 연다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;const r=body.data.request;r.status='REVIEWED';r.request_type='FAULT';r.fault_id='FLT-001';
 vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).includes('/case-comparison')?jsonResponse(200,{data:{request_id:r.id,hidden_case_count:0,items:[]}}):jsonResponse(200,body)));view();
 await screen.findByText('수신 부서와 할 일');
 expect(document.querySelector('.steps .st.cur')!.textContent).toMatch(/부서 전달/);
 expect([...document.querySelectorAll('.steps .st.done')].map(e=>e.textContent)).toEqual(['✓작성','✓검토']);
 const fold=screen.getByRole('button',{name:/근거 비교/});expect(fold.getAttribute('aria-expanded')).toBe('false');
 fireEvent.click(screen.getByTitle('근거 비교 열기'));
 await waitFor(()=>expect(fold.getAttribute('aria-expanded')).toBe('true'));
});

test('근거 비교에서 후보에 없는 문의도 문의·해결 사례 ID로 직접 넣는다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;const r=body.data.request;r.request_type='FAULT';r.fault_id='FLT-001';r.doc_signal=null;
 const fetch=vi.fn(async(url:RequestInfo|URL,_init?:RequestInit)=>{const path=String(url);
  if(path.endsWith('/me'))return new Response(JSON.stringify({data:{roles:['cs_agent'],dept:null,managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}});
  if(path.includes('/case-comparison'))return jsonResponse(200,{data:{request_id:r.id,hidden_case_count:0,items:[]}});
  return jsonResponse(200,body);});
 vi.stubGlobal('fetch',fetch);view();
 fireEvent.click(await screen.findByTitle('근거 비교 열기'));
 const input=await screen.findByLabelText('추가할 문의 ID');
 fireEvent.change(input,{target:{value:'22222222-2222-4222-8222-222222222299'}});fireEvent.change(screen.getByLabelText('추가할 해결 사례 ID'),{target:{value:'44444444-4444-4444-8444-444444444499'}});
 const add=screen.getAllByRole('button',{name:'넣기'}).find(b=>b.closest('.cmp-add'))!;await waitFor(()=>expect((add as HTMLButtonElement).disabled).toBe(false));fireEvent.click(add);
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).endsWith('/evidence')&&init?.method==='POST')).toBe(true));
 const call=fetch.mock.calls.find(([url])=>String(url).endsWith('/evidence'))!;expect(JSON.parse(call[1]!.body as string)).toEqual({ticket_id:'22222222-2222-4222-8222-222222222299',case_id:'44444444-4444-4444-8444-444444444499',version:r.version});
});

test('받은 부서에 보이는 limited 근거는 문의 링크 없이 번호만, 고객은 - 로 보인다',async()=>{
 const base={row_kind:'EVIDENCE',ticket_subject:null,ticket_received_at:'2026-09-01T00:00:00Z',product_code:'P',case_id:null,case_external_id:null,symptom:'Oil leak',root_cause_type:'WEAR_PART',root_cause:null,action_taken:'Replace gasket',resolved_by_dept:'TS_TRF',known_at:null,fw_version_at_issue:null,onsite_visit:false};
 const items=[{...base,ticket_id:'11111111-0000-4000-8000-000000000000',ticket_external_id:'T-OPEN',customer_external_id:'CUST-0001',limited:false},{...base,ticket_id:'22222222-0000-4000-8000-000000000000',ticket_external_id:'T-LIMIT',customer_external_id:null,limited:true}];
 const fault=structuredClone(source) as unknown as ImprovementDetail;fault.data.request.request_type='FAULT';
 vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).includes('/case-comparison')?jsonResponse(200,{data:{request_id:'test',hidden_case_count:0,items}}):jsonResponse(200,fault)));view();
 // 판단할 것이 없으면(모두 같음, 후보 없음) 접혀 시작한다. 머리를 눌러 편다.
 fireEvent.click(await screen.findByRole('button',{name:/근거 비교/}));
 const table=within(await screen.findByRole('table',{name:'근거 비교'}));
 expect(table.getByRole('link',{name:'T-OPEN'})).toBeTruthy();
 expect(table.queryByRole('link',{name:'T-LIMIT'})).toBeNull();expect(table.getByText('T-LIMIT')).toBeTruthy();
 expect(table.getByText('CUST-0001')).toBeTruthy();expect(table.getByTitle('이관받지 않은 문의의 고객은 보이지 않습니다').textContent).toBe('-');
});

test('처리할 것이 없는 사용자(C/S 담당자)에게는 부서 줄에 그 부서의 지금 상태가 보인다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;const r=body.data.request;
 r.status='SENT';r.version=3;r.current_round=1;body.data.next_owners=[{actor:'DEPARTMENT',user_id:null,dept_code:'TS_TRF',task:'REPLY'}];
 const dept=(code:string,status:string)=>({round:1,dept_code:code,status,carried_over:false,sent_version:2,reply_due_at:'2999-01-01T00:00:00Z',rejection_reviewed_at:null,rejection_reviewed_by:null});
 body.data.departments=[dept('TS_TRF','SENT'),dept('QA_CLAIM','ADOPTED')] as typeof body.data.departments;
 vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:['cs_agent'],dept:null,managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}}):jsonResponse(200,body)));view();
 await screen.findByText('수신 부서와 할 일');
 const cells=await waitFor(()=>{const found=[...document.querySelectorAll('td.acts')].map(td=>td.textContent);expect(found).toEqual(['회신 대기','채택']);return found;});
 expect(cells).toHaveLength(2);
});

test('회신을 마친 부서 줄에는 지난 처리 대신 다음 처리(조치 기록)가 보인다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;const r=body.data.request;
 r.status='ADOPTED';r.version=4;r.current_round=1;body.data.next_owners=[{actor:'DEPARTMENT',user_id:null,dept_code:'TS_TRF',task:'RECORD_ACTION'}];
 body.data.departments=[{round:1,dept_code:'TS_TRF',status:'ADOPTED',carried_over:false,sent_version:2,reply_due_at:'2999-01-01T00:00:00Z',rejection_reviewed_at:null,rejection_reviewed_by:null}];
 vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:['dept_staff'],dept:'TS_TRF',managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}}):jsonResponse(200,body)));view();
 await screen.findByText('수신 부서와 할 일');
 await waitFor(()=>expect([...document.querySelectorAll('td.acts')].map(td=>td.textContent)).toEqual(['조치 기록']));
});

test('작성 중이면 개선 요청 작성 상자에 검토 완료가 있고, 누르면 입력 없이 기록한 뒤 같은 자리에 부서 전달이 뜬다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;const r=body.data.request;
 r.status='DRAFT';r.version=2;r.request_type='FAULT';r.proposed_depts=['TS_TRF'];r.problem_statement='합성 문제';body.data.next_owners=[{actor:'CS_OWNER',user_id:null,dept_code:null,task:'REVIEW'}];body.data.departments=[];
 const reviewed=structuredClone(body);reviewed.data.request.status='REVIEWED';reviewed.data.request.version=3;reviewed.data.next_owners=[{actor:'CS_OWNER',user_id:null,dept_code:null,task:'SEND'}];
 const fetch=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
  const path=String(url);
  if(path.endsWith('/me'))return new Response(JSON.stringify({data:{roles:['cs_agent'],dept:null,managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}});
  if(path.endsWith('/review')&&init?.method==='POST')return jsonResponse(200,reviewed);
  if(path.includes('/case-comparison'))return jsonResponse(200,{data:{request_id:r.id,hidden_case_count:0,items:[]}});
  return jsonResponse(200,body);
 });
 vi.stubGlobal('fetch',fetch);view();
 const box=(await screen.findByRole('button',{name:'검토 완료'})).closest('.ai-apply')!;
 fireEvent.click(within(box as HTMLElement).getByRole('button',{name:'검토 완료'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([u,i])=>String(u).endsWith('/review')&&(i as RequestInit|undefined)?.method==='POST')).toBe(true));
 expect(screen.queryByRole('form',{name:/검토 완료 기록 입력/})).toBeNull();
 const send=await screen.findByRole('button',{name:'부서 전달'});
 expect(send.closest('.ai-apply')).toBeTruthy();
});

test('근거 비교는 판단할 것이 있어도 접힌 채로 시작하고 머리를 누르면 펼친다',async()=>{
 const row=(ticket:string,fw:string)=>({row_kind:'EVIDENCE',ticket_id:`${ticket}-0000-4000-8000-000000000000`.slice(0,36),ticket_external_id:ticket,ticket_subject:null,ticket_received_at:'2026-09-01T00:00:00Z',customer_external_id:`C-${ticket}`,product_code:'P',case_id:null,case_external_id:null,symptom:null,root_cause_type:'SETTING',root_cause:null,action_taken:null,resolved_by_dept:'TS_LV',known_at:null,fw_version_at_issue:fw,onsite_visit:false});
 const fault=structuredClone(source) as unknown as ImprovementDetail;fault.data.request.request_type='FAULT';
 vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).includes('/case-comparison')?jsonResponse(200,{data:{request_id:'test',hidden_case_count:0,items:[row('T-1','1.72'),row('T-2','1.72'),row('T-3','1.80')]}}):jsonResponse(200,fault)));view();
 const head=await screen.findByRole('button',{name:/근거 비교/});
 await screen.findByText(/판단 필요/);
 expect(head.getAttribute('aria-expanded')).toBe('false');expect(screen.queryByRole('table',{name:'근거 비교'})).toBeNull();
 fireEvent.click(head);expect(await screen.findByRole('table',{name:'근거 비교'})).toBeTruthy();
});

test('부서 담당자에게는 개선 요청 작성의 내용 수정·AI 초안 작성 단추가 없다(C/S가 보낸 요청서를 읽기만)',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;const r=body.data.request;
 r.status='SENT';r.version=3;r.current_round=1;body.data.departments=[{round:1,dept_code:'TS_TRF',status:'SENT',carried_over:false,sent_version:2,reply_due_at:'2999-01-01T00:00:00Z',rejection_reviewed_at:null,rejection_reviewed_by:null}];
 vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:['dept_staff'],dept:'TS_TRF',managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}}):jsonResponse(200,body)));view();
 await screen.findByText('수신 부서와 할 일');
 await waitFor(()=>expect(screen.queryByRole('button',{name:'내용 수정'})).toBeNull());
 expect(screen.queryByRole('button',{name:/AI 초안 작성/})).toBeNull();
});
