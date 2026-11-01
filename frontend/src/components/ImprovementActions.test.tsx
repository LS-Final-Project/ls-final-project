import { afterEach, expect, test, vi } from 'vitest';
import { useState } from 'react';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import ImprovementActions from './ImprovementActions';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import type { ImprovementDetail } from '../api/cs-improvements';
import examples from '../mocks/generated/cs.json';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const fixture=examples.find(x=>x.path==='/api/v1/cs/improvement-requests/dddddddd-dddd-4ddd-8ddd-ddddddddddd1')!.body as unknown as ImprovementDetail;
const ID='dddddddd-dddd-4ddd-8ddd-ddddddddddd1';
const OWNER='99999999-9999-4999-8999-999999999991';
type Dept=ImprovementDetail['data']['departments'][number];
const dept=(code:string,status:Dept['status'],extra:Partial<Dept>={}):Dept=>({round:1,dept_code:code,status,carried_over:false,sent_version:2,reply_due_at:'2999-01-01T00:00:00Z',rejection_reviewed_at:null,rejection_reviewed_by:null,...extra});
function detail(change:(d:ImprovementDetail['data'])=>void=()=>{}){const d=structuredClone(fixture);change(d.data);return d;}
const sent=(d:ImprovementDetail['data'],...depts:Dept[])=>{d.request.status=depts.length?'SENT':'REVIEWED';d.request.version=3;d.request.current_round=1;d.departments=depts;d.next_owners=[];};
type Identity={roles:string[];dept?:string|null;managed_departments?:string[]};
const meResponse=(id:Identity|null,csrf=true)=>id?new Response(JSON.stringify({data:{dept:null,managed_departments:[],...id}}),{headers:csrf?{'X-CSRF-Token':'synthetic-csrf'}:{}}):jsonResponse(401,errorBody('SESSION','세션 없음'));
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');clearSessionCsrfToken();});

function setup(data:ImprovementDetail,identity:Identity|null,write:(url:string,init:RequestInit)=>Response|Promise<Response>=()=>jsonResponse(200,detail(d=>{d.request.version=data.data.request.version+1;}))){
  const saved=vi.fn();const reload=vi.fn();const editing=vi.fn();
  const fetch=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
    const path=String(url);
    if(path.endsWith('/me'))return meResponse(identity);
    if(path.includes('/platform/assignees'))return jsonResponse(200,{data:{items:[{user_id:OWNER,display_name:'합성 담당자'},{user_id:data.data.request.cs_owner,display_name:'현재 합성 담당자'}]}});
    if(init?.method)return write(path,init);
    return jsonResponse(200,{data:{}});
  });
  vi.stubGlobal('fetch',fetch);setApiMode('live');
  // 결과 불명 상태는 상세 화면처럼 바깥에서 들고 있다. 해제는 화면 시험(CsImprovements.test)에서 확인한다.
  function Host(){const [uncertain,setUncertain]=useState(false);return <ImprovementActions data={data.data} onSaved={saved} onReload={reload} onEditingChange={editing} uncertain={uncertain} onUncertain={()=>setUncertain(true)}/>;}
  renderAt('/','/',<Host/>);
  const writes=()=>(fetch.mock.calls as unknown as [string,RequestInit|undefined][]).filter((call):call is [string,RequestInit]=>!!call[1]?.method);
  return {fetch,saved,reload,editing,writes};
}
const row=(label:string)=>screen.getAllByRole('row').find(r=>within(r).queryAllByRole('cell')[0]?.textContent===label)!;
const save=()=>fireEvent.click(screen.getByRole('button',{name:'기록 저장'}));
const saveButton=()=>screen.getByRole('button',{name:'기록 저장'}) as HTMLButtonElement;

test('작성 중 요청은 C/S 담당자에게 검토만 열고 나머지는 이유를 적는다',async()=>{
  const {saved,writes,editing}=setup(detail(),{roles:['cs_agent']});
  fireEvent.click(await screen.findByRole('button',{name:'검토 완료 기록'}));
  expect(within(row('부서 전달 기록')).getByText(/검토를 먼저 기록해야/)).toBeTruthy();
  expect(within(row('후속 확인 결과와 완료 기록')).queryByRole('button')).toBeNull();
  expect(editing).toHaveBeenLastCalledWith(true);
  save();await waitFor(()=>expect(saved).toHaveBeenCalledTimes(1));
  const [url,init]=writes()[0];expect(url).toBe(`/api/v1/cs/improvement-requests/${ID}/review`);expect(init.method).toBe('POST');
  expect(JSON.parse(init.body as string)).toEqual({version:1});expect(init.headers).toMatchObject({'X-CSRF-Token':'synthetic-csrf'});
  const status=screen.getByRole('status');expect(status.textContent).toMatch(/판본 2/);expect(status.textContent).not.toMatch(/승인|적용했/);expect(status.textContent).toMatch(/고객 장비 적용을 뜻하지 않습니다/);
});

test('검토 중 바꾼 제안 부서만 보내고 고장 요청은 부서를 비우지 못한다',async()=>{
  const {writes,saved}=setup(detail(),{roles:['cs_agent']});
  fireEvent.click(await screen.findByRole('button',{name:'검토 완료 기록'}));
  fireEvent.click(screen.getByLabelText(/품질보증\/클레임/));expect(saveButton().disabled).toBe(true);
  fireEvent.click(screen.getByLabelText(/변압기 기술지원/));save();await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(JSON.parse(writes()[0][1].body as string)).toEqual({version:1,proposed_depts:['TS_TRF']});
});

test('받는 부서가 없는 문서 요청은 부서를 고른 뒤에만 전달을 기록한다',async()=>{
  const data=detail(d=>{sent(d);d.request.request_type='DOCUMENT';d.request.proposed_depts=[];d.request.receiver_unconfirmed=true;});
  const {writes,saved}=setup(data,{roles:['cs_agent']});
  fireEvent.click(await screen.findByRole('button',{name:'부서 전달 기록'}));
  expect(screen.getByText(/받는 부서를 고르세요/)).toBeTruthy();expect(saveButton().disabled).toBe(true);
  fireEvent.click(screen.getByLabelText(/저압기기 기술지원/));fireEvent.change(screen.getByLabelText('회신 기한(일)'),{target:{value:'91'}});expect(saveButton().disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('회신 기한(일)'),{target:{value:'5'}});save();await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(writes()[0][0]).toMatch(/\/send$/);expect(JSON.parse(writes()[0][1].body as string)).toEqual({version:3,dept_codes:['TS_LV'],reply_due_days:5});
});

test('받는 부서 담당자는 자기 부서의 수신 확인만 할 수 있다',async()=>{
  const data=detail(d=>sent(d,dept('QA_CLAIM','SENT')));
  const {writes,saved}=setup(data,{roles:['dept_staff'],dept:'QA_CLAIM'});
  fireEvent.click(await screen.findByRole('button',{name:'수신 확인'}));
  expect(within(row('회신(채택·추가 정보 요청·반려)')).getByText(/'수신 확인'일 때만/)).toBeTruthy();
  expect(within(row('개선 조치 기록')).queryByRole('button')).toBeNull();
  expect(screen.queryByText('검토 완료 기록')).toBeNull();
  save();await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(writes()[0][0]).toBe(`/api/v1/cs/improvement-requests/${ID}/departments/QA_CLAIM/acknowledge`);expect(JSON.parse(writes()[0][1].body as string)).toEqual({version:3});
});

test.each([
  [{roles:['dept_staff'],dept:'TS_LV'},/현재 회차에 내 부서가/],
  [{roles:['manager'],managed_departments:['QA_CLAIM']},/C\/S 부서 관리자만/],
] as [Identity,RegExp][])('권한 밖 역할 %j는 실행 버튼 없이 이유만 본다',async(identity,reason)=>{
  const {writes}=setup(detail(d=>sent(d,dept('QA_CLAIM','SENT'))),identity);
  await screen.findByText(reason);
  expect(within(screen.getByRole('table')).queryAllByRole('button')).toHaveLength(0);expect(writes()).toHaveLength(0);
});

test('역할이 없는 세션은 동작을 보이지 않는다',async()=>{
  setup(detail(),{roles:['sales']});await screen.findByText(/할 수 있는 동작이 없습니다/);expect(screen.queryByRole('table')).toBeNull();
});

test('화면에 버튼이 보여도 쓰기 직전 세션 역할이 바뀌었으면 보내지 않는다',async()=>{
  const {fetch,writes}=setup(detail(),{roles:['cs_agent']});
  fireEvent.click(await screen.findByRole('button',{name:'검토 완료 기록'}));
  fetch.mockImplementation(async url=>String(url).endsWith('/me')?meResponse({roles:['dept_staff'],dept:'QA_CLAIM'}):jsonResponse(200,detail()));
  save();expect((await screen.findByRole('alert')).textContent).toMatch(/C\/S 담당자 세션이 필요/);expect(writes()).toHaveLength(0);
});

test('CSRF 없는 세션은 쓰기를 보내지 않는다',async()=>{
  const {fetch,writes}=setup(detail(),{roles:['cs_agent']});
  fireEvent.click(await screen.findByRole('button',{name:'검토 완료 기록'}));
  fetch.mockImplementation(async()=>meResponse({roles:['cs_agent']},false));
  save();await screen.findByRole('alert');expect(writes()).toHaveLength(0);
});

test('반려 회신은 사유가 있어야 보낸다',async()=>{
  const {writes,saved}=setup(detail(d=>sent(d,dept('QA_CLAIM','ACKNOWLEDGED'))),{roles:['dept_staff'],dept:'QA_CLAIM'});
  fireEvent.click(await screen.findByRole('button',{name:'회신(채택·추가 정보 요청·반려)'}));
  fireEvent.change(screen.getByLabelText('회신 유형'),{target:{value:'REJECTED'}});expect(saveButton().disabled).toBe(true);
  fireEvent.change(screen.getByLabelText(/^사유/),{target:{value:' 합성 반려 사유 '}});save();await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(writes()[0][0]).toMatch(/departments\/QA_CLAIM\/reply$/);expect(JSON.parse(writes()[0][1].body as string)).toEqual({version:3,reply_type:'REJECTED',reason:'합성 반려 사유'});
});

test('채택한 부서는 적용 범위와 함께 조치를 기록한다',async()=>{
  const {writes,saved}=setup(detail(d=>sent(d,dept('QA_CLAIM','ADOPTED'))),{roles:['dept_staff'],dept:'QA_CLAIM'});
  fireEvent.click(await screen.findByRole('button',{name:'개선 조치 기록'}));
  fireEvent.change(screen.getByLabelText('조치 내용'),{target:{value:'합성 조치'}});
  fireEvent.change(screen.getByLabelText('적용 대상 설명'),{target:{value:'합성 대상'}});
  fireEvent.change(screen.getByLabelText('실제 적용일'),{target:{value:'2026-10-30'}});
  fireEvent.change(screen.getByLabelText('조치 근거'),{target:{value:'합성 근거'}});
  fireEvent.change(screen.getByLabelText('적용 범위 기준'),{target:{value:'FIRMWARE_VERSION'}});expect(saveButton().disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('적용 범위 값'),{target:{value:'1.72'}});save();await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(writes()[0][0]).toMatch(/departments\/QA_CLAIM\/action$/);
  expect(JSON.parse(writes()[0][1].body as string)).toEqual({version:3,action:'합성 조치',action_scope:'합성 대상',action_date:'2026-10-30',action_evidence:'합성 근거',scope_kind:'FIRMWARE_VERSION',scope_value:'1.72'});
});

test('다시 요청은 보완·반려 부서에 열고 기한 전 재촉은 이유를 적는다',async()=>{
  const data=detail(d=>sent(d,dept('QA_CLAIM','INFO_REQUESTED'),dept('TS_LV','SENT'),dept('TS_MV','ACKNOWLEDGED',{reply_due_at:'2000-01-01T00:00:00Z'}),dept('CERT','REJECTED')));
  const {writes,saved}=setup(data,{roles:['cs_agent']});
  await screen.findByText(/이 지난 뒤에만 재촉/);
  const rows=screen.getAllByRole('row');const target=(name:RegExp)=>rows.find(r=>name.test(r.textContent??''))!;
  expect(within(target(/저압기기 기술지원/)).queryByRole('button')).toBeNull();
  expect(within(target(/회신 재촉중압기기/)).getByRole('button')).toBeTruthy();
  expect(within(target(/반려 확인인증/)).getByRole('button')).toBeTruthy();
  fireEvent.click(within(target(/자료 보완 후 다시 요청/)).getByRole('button'));
  fireEvent.change(screen.getByLabelText('다시 요청하는 이유'),{target:{value:'합성 보완 자료 첨부'}});save();await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(writes()[0][0]).toMatch(/departments\/QA_CLAIM\/resend$/);expect(JSON.parse(writes()[0][1].body as string)).toEqual({version:3,reason:'합성 보완 자료 첨부',reply_due_days:7});
});

test('후속 확인 계획은 서버가 알려준 다음 할 일이 있을 때만 열고 완료는 후속 확인 중에만 연다',async()=>{
  const ready=detail(d=>{sent(d,dept('QA_CLAIM','ACTION_TAKEN'));d.request.status='ACTION_TAKEN';d.next_owners=[{actor:'CS_OWNER',user_id:null,dept_code:null,task:'PLAN_FOLLOWUP'}];});
  const {writes,saved}=setup(ready,{roles:['cs_agent']});
  fireEvent.click(await screen.findByRole('button',{name:'후속 확인 계획 기록'}));
  expect(within(row('후속 확인 결과와 완료 기록')).getByText(/계획을 먼저/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('후속 확인 계획'),{target:{value:'합성 계획'}});save();await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(writes()[0][0]).toMatch(/\/followup-plan$/);expect(JSON.parse(writes()[0][1].body as string)).toEqual({version:3,followup_plan:'합성 계획'});
  cleanup();
  const follow=detail(d=>{sent(d,dept('QA_CLAIM','ACTION_TAKEN'));d.request.status='FOLLOW_UP';});
  const second=setup(follow,{roles:['cs_agent']});
  fireEvent.click(await screen.findByRole('button',{name:'후속 확인 결과와 완료 기록'}));
  expect(screen.getByText(/고객별 적용·재발 여부를 확인했다는 뜻이 아닙니다/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('후속 확인 결과'),{target:{value:'합성 결과'}});save();await waitFor(()=>expect(second.saved).toHaveBeenCalled());
  expect(second.writes()[0][0]).toMatch(/\/complete$/);expect(JSON.parse(second.writes()[0][1].body as string)).toEqual({version:3,followup_result:'합성 결과'});
});

test('후속 확인 중에는 부서 처리를 닫고 이유를 적는다',async()=>{
  setup(detail(d=>{sent(d,dept('QA_CLAIM','ACTION_TAKEN'));d.request.status='FOLLOW_UP';}),{roles:['dept_staff'],dept:'QA_CLAIM'});
  expect((await screen.findAllByText(/후속 확인 단계에서는 부서 처리를 바꾸지 않습니다/)).length).toBe(3);
});

test('근거 추가는 고장 요청에 해결 사례를 요구하고 제외는 사유를 보낸다',async()=>{
  const {writes,saved}=setup(detail(),{roles:['cs_agent']});
  fireEvent.click(await screen.findByRole('button',{name:'근거 문의 추가'}));
  fireEvent.change(screen.getByLabelText('문의 ID'),{target:{value:'22222222-2222-4222-8222-222222222299'}});expect(saveButton().disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('해결 사례 ID'),{target:{value:'44444444-4444-4444-8444-444444444499'}});save();await waitFor(()=>expect(saved).toHaveBeenCalledTimes(1));
  expect(writes()[0][0]).toMatch(/\/evidence$/);expect(JSON.parse(writes()[0][1].body as string)).toEqual({version:1,ticket_id:'22222222-2222-4222-8222-222222222299',case_id:'44444444-4444-4444-8444-444444444499'});
  fireEvent.click(within(row('근거 제외')).getByRole('button'));
  fireEvent.change(screen.getByLabelText('제외 사유'),{target:{value:'합성 제외'}});save();await waitFor(()=>expect(saved).toHaveBeenCalledTimes(2));
  expect(writes()[1][0]).toBe(`/api/v1/cs/improvement-requests/${ID}/evidence/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1/remove`);expect(JSON.parse(writes()[1][1].body as string)).toEqual({version:1,reason:'합성 제외'});
});

test('C/S 부서 관리자는 후보 목록에서 담당자를 재배정한다',async()=>{
  const {writes,saved}=setup(detail(),{roles:['manager'],managed_departments:['GCS_L1']});
  fireEvent.click(await screen.findByRole('button',{name:'C/S 담당자 재배정'}));
  const select=await screen.findByLabelText('새 C/S 담당자');expect((screen.getByRole('option',{name:/현재 담당/}) as HTMLOptionElement).disabled).toBe(true);
  fireEvent.change(select,{target:{value:OWNER}});save();await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(writes()[0][0]).toMatch(/\/owner$/);expect(writes()[0][1].method).toBe('PUT');expect(JSON.parse(writes()[0][1].body as string)).toEqual({version:1,owner_user_id:OWNER});
});

test.each([[409,'CS_REQUEST_VERSION_CONFLICT'],[409,'CS_REQUEST_INVALID_TRANSITION'],[408,'TIMEOUT'],[503,'UNAVAILABLE']])('%i %s는 자동 재전송 없이 최신 상태 다시 읽기를 요구한다',async(status,code)=>{
  const {writes,saved,reload}=setup(detail(),{roles:['cs_agent']},()=>jsonResponse(status,errorBody(code,'합성 처리 실패')));
  fireEvent.click(await screen.findByRole('button',{name:'검토 완료 기록'}));save();
  expect((await screen.findByRole('alert')).textContent).toBe('합성 처리 실패');expect(saved).not.toHaveBeenCalled();
  expect(saveButton().disabled).toBe(true);save();expect(writes()).toHaveLength(1);
  fireEvent.click(screen.getByRole('button',{name:'입력 취소'}));
  expect((screen.getByRole('button',{name:'근거 문의 추가'}) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/다시 보내지 말고 최신 상태를 다시 읽으세요/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'입력 버리고 최신 상태 다시 읽기'}));expect(reload).toHaveBeenCalledTimes(1);expect(writes()).toHaveLength(1);
});

test('네트워크 결과 불명도 재전송을 막는다',async()=>{
  const {writes}=setup(detail(),{roles:['cs_agent']},()=>{throw new TypeError('network');});
  fireEvent.click(await screen.findByRole('button',{name:'검토 완료 기록'}));save();await screen.findByRole('alert');
  expect(saveButton().disabled).toBe(true);expect(screen.getByRole('button',{name:'입력 버리고 최신 상태 다시 읽기'})).toBeTruthy();expect(writes()).toHaveLength(1);
});

test('422는 입력을 고쳐 다시 보낼 수 있다',async()=>{
  let attempt=0;
  const {writes,saved}=setup(detail(),{roles:['cs_agent']},()=>++attempt===1?jsonResponse(422,errorBody('CS_EVIDENCE_INVALID','합성 근거 오류')):jsonResponse(200,detail()));
  fireEvent.click(await screen.findByRole('button',{name:'근거 문의 추가'}));
  fireEvent.change(screen.getByLabelText('문의 ID'),{target:{value:'22222222-2222-4222-8222-222222222299'}});fireEvent.change(screen.getByLabelText('해결 사례 ID'),{target:{value:'44444444-4444-4444-8444-444444444499'}});
  save();await screen.findByRole('alert');expect(saveButton().disabled).toBe(false);
  fireEvent.change(screen.getByLabelText('해결 사례 ID'),{target:{value:'44444444-4444-4444-8444-444444444498'}});save();await waitFor(()=>expect(saved).toHaveBeenCalledTimes(1));expect(writes()).toHaveLength(2);
});

test('저장 중 반복 클릭은 한 번만 보내고 이탈하면 요청을 취소한다',async()=>{
  let signal:AbortSignal|undefined;
  const {writes,saved}=setup(detail(),{roles:['cs_agent']},(_url,init)=>new Promise<Response>((_resolve,reject)=>{signal=init.signal as AbortSignal;signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));}));
  fireEvent.click(await screen.findByRole('button',{name:'검토 완료 기록'}));save();await waitFor(()=>expect(writes()).toHaveLength(1));
  fireEvent.click(screen.getByRole('button',{name:'기록 중'}));expect(writes()).toHaveLength(1);
  cleanup();expect(signal?.aborted).toBe(true);expect(saved).not.toHaveBeenCalled();
});

test('모의 응답에서는 세션 조회와 쓰기를 하지 않는다',()=>{
  const fetch=vi.fn();vi.stubGlobal('fetch',fetch);setApiMode('mock');
  renderAt('/','/',<ImprovementActions data={detail().data} onSaved={()=>{}} onReload={()=>{}}/>);
  expect(screen.getByText('모의 응답에서는 저장하지 않습니다.')).toBeTruthy();expect(screen.queryByRole('button',{name:'검토 완료 기록'})).toBeNull();expect(fetch).not.toHaveBeenCalled();
});

test('재요청·재발 신호는 판정 이름을 쉬운 말로 보이고 새 회차를 열지 않는다',async()=>{
  const data=detail(d=>sent(d,dept('QA_CLAIM','SENT')));
  const {fetch}=setup(data,{roles:['cs_agent']});
  fetch.mockImplementation(async url=>String(url).endsWith('/me')?meResponse({roles:['cs_agent']}):jsonResponse(200,{data:{request_id:ID,as_of:'2026-02-24T00:00:00Z',phase:'BEFORE_ACTION',decision:'REQUEST_AGAIN',reason_code:'NEW_RECORDS_3',baseline:{last_sent_at:'2025-11-16T00:00:00Z',round:1,action_dates:[]},counts:{new_since_sent:3,threshold:3,candidate_index:1,recurrence:0,pre_action_unit:0,application_unknown:0},records:[{ticket_id:'t-1',case_id:'c-1',known_at:'2026-01-09T00:00:00Z',customer_id:null,class:'NEW_SINCE_SENT',reason_code:null,basis:null,action_dept:null}],update_notice_customers:[],next_action:'OPEN_ROUND_REQUEST_AGAIN'}}));
  fireEvent.click(await screen.findByRole('button',{name:'신호 조회'}));
  await screen.findByText(/재요청 후보 · 직전 전달 뒤 새 해결 기록 3건 이상/);
  expect(screen.getByText('3/3 (후보 1번)')).toBeTruthy();expect(screen.getByText('직전 전달 뒤 새 기록')).toBeTruthy();expect(screen.getByText(/이 화면에서는 새 회차를 열지 않습니다/)).toBeTruthy();
  expect(document.body.textContent).not.toMatch(/REQUEST_AGAIN|NEW_RECORDS_3|NEW_SINCE_SENT/);
  expect((fetch.mock.calls as unknown as [string][]).some(([url])=>url===`/api/v1/cs/improvement-requests/${ID}/followup-signals`)).toBe(true);
});

test('문서 요청은 재요청·재발 신호를 조회하지 않는다',async()=>{
  const {fetch}=setup(detail(d=>{d.request.request_type='DOCUMENT';}),{roles:['cs_agent']});
  await screen.findByText('문서 요청은 재요청·재발 판정 대상이 아닙니다.');expect(screen.queryByRole('button',{name:'신호 조회'})).toBeNull();
  expect((fetch.mock.calls as unknown as [string][]).some(([url])=>url.includes('followup-signals'))).toBe(false);
});

test('작성 내용 수정이 열려 있으면 처리 동작을 시작하지 않는다',async()=>{
  vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/me')?meResponse({roles:['cs_agent']}):jsonResponse(200,{data:{}})));setApiMode('live');
  renderAt('/','/',<ImprovementActions data={detail().data} onSaved={()=>{}} onReload={()=>{}} locked/>);
  expect(((await screen.findByRole('button',{name:'검토 완료 기록'})) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/작성 내용 수정이 열려 있어/)).toBeTruthy();
});

test('담당자 재배정은 후보 목록에 있는 사람만 고를 수 있고 후보가 없으면 저장하지 않는다',async()=>{
  const data=detail();
  const {fetch,writes}=setup(data,{roles:['manager'],managed_departments:['GCS_L1']});
  fetch.mockImplementation(async url=>String(url).endsWith('/me')?meResponse({roles:['manager'],managed_departments:['GCS_L1']}):jsonResponse(200,{data:{items:[{user_id:data.data.request.cs_owner,display_name:'현재 합성 담당자'}]}}));
  fireEvent.click(await screen.findByRole('button',{name:'C/S 담당자 재배정'}));
  await screen.findByText('재배정할 수 있는 C/S 담당자가 없습니다.');expect(screen.getByText('현재 담당: 현재 합성 담당자')).toBeTruthy();
  expect(screen.queryByRole('textbox')).toBeNull();expect(saveButton().disabled).toBe(true);save();expect(writes()).toHaveLength(0);
});
