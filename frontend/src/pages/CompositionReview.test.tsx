import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { CompositionPanel } from './CompositionReview';
import examples from '../mocks/generated/tft.json';
import type { ProjectDetail } from '../api/tft';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
const id='33333333-3333-4333-8333-333333333333';
const path=`/api/v1/tft/projects/${id}`;
const original=examples.find(x=>x.path===path)!.body.data as unknown as ProjectDetail;
function fixture(state='READY_FOR_FINAL_REVIEW'){
 const project=structuredClone(original);
 const report={project_id:id,composition_version:1,roles:[{role_id:project.required_roles[0].id,candidates:[],reasons:['NO_RECOMMENDATION'],next_organization_ids:[],next_user_id:project.responsible_user_id,next_action:'DEPARTMENT_CHECK',confirmed_count:0,required_count:1,complete:false}],confirmed_role_count:0,total_role_count:2,state,complete:false};
 return {project,report};
}
function setup(options:{state?:string;userId?:string;post?:(init?:RequestInit)=>Promise<Response>;deny?:boolean}={}){
 const f=fixture(options.state);
 const fetchMock=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
  const p=String(url);
  if(p==='/api/v1/platform/me')return jsonResponse(200,{data:{id:options.userId??f.project.responsible_user_id,roles:['sales'],display_name:'검증 사용자'}});
  if(init?.method==='POST')return options.post?options.post(init):jsonResponse(200,{data:{project_version:2}});
  if(p===path)return options.deny?jsonResponse(403,errorBody('FORBIDDEN','접근 거부')):jsonResponse(200,{data:f.project});
  if(p.endsWith('/verification'))return jsonResponse(200,{data:f.report});
  if(p.includes('/decisions?'))return jsonResponse(200,{data:[]});
  if(p.endsWith('/compositions/1'))return jsonResponse(200,{data:{...f.project.current_composition,note:'과거 저장 근거'}});
  throw new Error(p);
 });
 vi.stubGlobal('fetch',fetchMock);setApiMode('live');renderAt('/review','/review',<CompositionPanel projectId={id}/>);return fetchMock;
}
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');});
const button=(name:string)=>screen.getByRole('button',{name}) as HTMLButtonElement;
async function ready(){await waitFor(()=>expect(button('반려').disabled).toBe(false));}
test('미추천·부분 확정을 전체 완료로 표시하지 않는다',async()=>{setup({state:'PARTIALLY_CONFIRMED'});await screen.findByText('부분 확정 — 전체 미확정');expect((await screen.findAllByText('추천 후보 없음')).length).toBeGreaterThan(0);expect(button('최종 승인').disabled).toBe(true);});
test('현재 책임자가 아니면 결정을 차단한다',async()=>{setup({userId:'someone-else'});await screen.findByText(/현재 지정된 책임자만/);expect(button('최종 승인').disabled).toBe(true);expect(button('반려').disabled).toBe(true);});
test('프로젝트 접근 거부 시 구성·검증 조회를 실행하지 않는다',async()=>{const f=setup({deny:true});await screen.findByText('권한이 없습니다');expect(f.mock.calls.some(([url])=>String(url).endsWith('/verification'))).toBe(false);});
test('결정 취소는 POST 없이 사유를 보존한다',async()=>{const f=setup();await ready();fireEvent.change(screen.getByLabelText('결정 사유'),{target:{value:'판단 근거'}});fireEvent.click(button('최종 승인'));fireEvent.click(button('취소'));expect((screen.getByLabelText('결정 사유') as HTMLTextAreaElement).value).toBe('판단 근거');expect(f.mock.calls.some(([,i])=>i?.method==='POST')).toBe(false);});
test('승인에 현재 판본과 멱등 키를 보내고 저장 후 새로 조회한다',async()=>{const f=setup();await ready();fireEvent.change(screen.getByLabelText('결정 사유'),{target:{value:'검토 완료'}});fireEvent.click(button('최종 승인'));fireEvent.click(button('최종 승인 확인'));await screen.findByText('결정을 저장했습니다. 최신 구성과 검증 상태를 확인하세요.');const [,init]=f.mock.calls.find(([,i])=>i?.method==='POST')!;expect(JSON.parse(init!.body as string)).toEqual({composition_version:1,reason:'검토 완료'});expect((init!.headers as Record<string,string>)['If-Match']).toBe('"1"');expect((init!.headers as Record<string,string>)['Idempotency-Key']).toBeTruthy();expect(f.mock.calls.filter(([u])=>String(u)===path).length).toBeGreaterThan(1);});
test('412는 자동 재전송하지 않고 사유를 보존하고 재검토를 요구한다',async()=>{const f=setup({post:async()=>jsonResponse(412,errorBody('VERSION_CONFLICT','판본 충돌'))});await ready();fireEvent.change(screen.getByLabelText('결정 사유'),{target:{value:'보존할 사유'}});fireEvent.click(button('반려'));fireEvent.click(button('반려 확인'));await screen.findByText(/판본 또는 상태가 변경/);expect((screen.getByLabelText('결정 사유') as HTMLTextAreaElement).value).toBe('보존할 사유');expect(f.mock.calls.filter(([,i])=>i?.method==='POST')).toHaveLength(1);expect(screen.queryByRole('button',{name:'반려 확인'})).toBeNull();});
test('불확실한 503 재시도는 같은 키를 사용한다',async()=>{let attempts=0;const f=setup({post:async()=>++attempts===1?jsonResponse(503,errorBody('UNAVAILABLE','잠시 실패')):jsonResponse(200,{data:{project_version:2}})});await ready();fireEvent.change(screen.getByLabelText('결정 사유'),{target:{value:'동일 요청'}});fireEvent.click(button('반려'));fireEvent.click(button('반려 확인'));await screen.findByText('잠시 실패');await waitFor(()=>expect(button('반려 확인').disabled).toBe(false));fireEvent.click(button('반려 확인'));await screen.findByText(/결정을 저장했습니다/);const calls=f.mock.calls.filter(([,i])=>i?.method==='POST');expect((calls[0][1]!.headers as Record<string,string>)['Idempotency-Key']).toBe((calls[1][1]!.headers as Record<string,string>)['Idempotency-Key']);});
test('과거 구성안은 현재와 별도로 조회한다',async()=>{setup();await ready();fireEvent.change(screen.getByLabelText('비교할 판본'),{target:{value:'1'}});fireEvent.click(button('판본 조회'));await screen.findByText('과거 저장 근거');expect(screen.getByRole('heading',{name:'현재 구성안'})).toBeTruthy();expect(screen.getByRole('heading',{name:'과거 구성안'})).toBeTruthy();});
test('저장 중 반복 클릭은 한 번만 전송한다',async()=>{let finish!:(value:Response)=>void;const f=setup({post:()=>new Promise(resolve=>{finish=resolve;})});await ready();fireEvent.change(screen.getByLabelText('결정 사유'),{target:{value:'보완 필요'}});fireEvent.click(button('반려'));fireEvent.click(button('반려 확인'));const busy=await screen.findByRole('button',{name:'저장 중'});fireEvent.click(busy);expect(f.mock.calls.filter(([,i])=>i?.method==='POST')).toHaveLength(1);finish(jsonResponse(200,{data:{}}));await screen.findByText(/결정을 저장했습니다/);});
