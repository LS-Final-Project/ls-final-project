import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { EvidenceForm } from './DepartmentRequest';
import type { ProjectDetail } from '../api/tft';
import { setApiMode } from '../api/client';
import { jsonResponse, errorBody, renderApp } from '../test/render';
import examples from '../mocks/generated/tft.json';

const projectId='33333333-3333-4333-8333-333333333333';
const path=`/api/v1/tft/projects/${projectId}`;
const example=examples.find(e=>e.path===path)!.body;
function setup(onPost:(init:RequestInit)=>Response=()=>jsonResponse(200,{data:{id:'saved',request_version:2}}),identity:()=>Response|Promise<Response>=()=>jsonResponse(200,{data:{user_id:'dept',roles:['dept_staff'],dept:'CON'}})){
  setApiMode('live');
  const data=structuredClone(example.data) as unknown as ProjectDetail;
  const req=data.staffing_requests[0] as unknown as Record<string,unknown>;
  req.organization_dept_code='CON';req.status='ISSUED';req.version=1;
  data.recommendations=[];
  const fetchMock=vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
    const url=String(input);
    if(url==='/api/v1/platform/me')return identity();
    if(init?.method==='POST')return onPost(init);
    if(url===path)return jsonResponse(200,{data});
    if(url.includes('/candidates'))return jsonResponse(200,{data:{items:[{id:'candidate',display_name:'합성 후보',job_title:'시공',data_origin:'synthetic'}],next_cursor:null}});
    if(url.includes('/department-requests'))return jsonResponse(200,{data:{items:[req],next_cursor:null}});
    return jsonResponse(404,errorBody('NOT_FOUND','없음'));
  });
  vi.stubGlobal('fetch',fetchMock);
  return {data,req,fetchMock,route:`/tft/projects/${projectId}/requests/${req.id}`};
}
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');});

async function fillRecommendation(){await screen.findByRole('option',{name:/합성 후보/});fireEvent.change(screen.getByLabelText('부서 후보'),{target:{value:'candidate'}});fireEvent.change(screen.getByLabelText('추천 이유'),{target:{value:'실제 시공 업무 수행'}});}

test('부서 요청 목록에서 해당 요청으로 연결한다',async()=>{const s=setup();renderApp('/tft/requests');const link=await screen.findByRole('link',{name:new RegExp(String(s.req.organization_name))});expect(link.getAttribute('href')).toBe(s.route);});

test('권한 확인 전에는 프로젝트나 후보를 조회하지 않는다',async()=>{const s=setup(undefined,()=>new Promise(()=>{}));renderApp(s.route);await screen.findByText('접근 권한 확인 중');expect(s.fetchMock.mock.calls.every(([url])=>url==='/api/v1/platform/me')).toBe(true);expect(screen.queryByText('추천 저장')).toBeNull();});

test.each([401,403,503])('사용자 확인 실패 %s에서 입력하지 못한다',async status=>{const s=setup(undefined,()=>jsonResponse(status,errorBody('AUTH','실패')));renderApp(s.route);await screen.findByRole('alert');expect(screen.queryByLabelText('추천 이유')).toBeNull();});

test('취소는 입력을 지우고 요청을 보내지 않는다',async()=>{const s=setup();renderApp(s.route);await fillRecommendation();fireEvent.click(screen.getByRole('button',{name:'취소'}));expect((screen.getByLabelText('추천 이유') as HTMLTextAreaElement).value).toBe('');expect(s.fetchMock.mock.calls.filter(([,i])=>i?.method==='POST')).toHaveLength(0);});

test('성공 시 기록을 새로 조회하고 입력을 비운다',async()=>{const s=setup();renderApp(s.route);await fillRecommendation();fireEvent.click(screen.getByRole('button',{name:'추천 저장'}));await screen.findByText(/저장했습니다/);expect((screen.getByLabelText('추천 이유') as HTMLTextAreaElement).value).toBe('');const post=s.fetchMock.mock.calls.find(([,i])=>i?.method==='POST')![1]!;expect((post.headers as Record<string,string>)['If-Match']).toBe('"1"');expect(JSON.parse(String(post.body))).toEqual({candidate_id:'candidate',rationale:'실제 시공 업무 수행'});});

test('503 재시도는 입력과 멱등 키를 보존한다',async()=>{let count=0;const s=setup(()=>++count===1?jsonResponse(503,errorBody('UNAVAILABLE','저장 연결 실패')):jsonResponse(200,{data:{id:'saved'}}));renderApp(s.route);await fillRecommendation();fireEvent.click(screen.getByRole('button',{name:'추천 저장'}));await screen.findByText('저장 연결 실패');expect((screen.getByLabelText('추천 이유') as HTMLTextAreaElement).value).toBe('실제 시공 업무 수행');fireEvent.click(screen.getByRole('button',{name:'추천 저장'}));await screen.findByText(/저장했습니다/);const posts=s.fetchMock.mock.calls.filter(([,i])=>i?.method==='POST');expect((posts[0][1]!.headers as Record<string,string>)['Idempotency-Key']).toBe((posts[1][1]!.headers as Record<string,string>)['Idempotency-Key']);});

test('412 뒤 입력을 보존하며 최신 판본을 확인하게 한다',async()=>{const s=setup(()=>jsonResponse(412,errorBody('VERSION_CONFLICT','판본 충돌')));renderApp(s.route);await fillRecommendation();fireEvent.click(screen.getByRole('button',{name:'추천 저장'}));await screen.findByText(/최신 판본을 다시 불러왔습니다/);expect((screen.getByLabelText('추천 이유') as HTMLTextAreaElement).value).toBe('실제 시공 업무 수행');await waitFor(()=>expect(s.fetchMock.mock.calls.filter(([u])=>u===path).length).toBeGreaterThan(1));});

test('권한 회수 후 재입력을 차단한다',async()=>{const s=setup(()=>jsonResponse(403,errorBody('FORBIDDEN','권한 회수')));renderApp(s.route);await fillRecommendation();fireEvent.click(screen.getByRole('button',{name:'추천 저장'}));await screen.findByText('권한이 없습니다');expect((screen.getByLabelText('추천 이유').closest('fieldset') as HTMLFieldSetElement).disabled).toBe(true);});

test('가용성 확인의 누락 입력과 역전 기간을 거부한다',()=>{const save=vi.fn();render(<EvidenceForm disabled={false} competencies={[]} onSave={save} />);fireEvent.change(screen.getByLabelText('확인 종류'),{target:{value:'availability'}});fireEvent.change(screen.getByLabelText('확인 상태'),{target:{value:'CONFIRMED'}});fireEvent.change(screen.getByLabelText('확인 근거'),{target:{value:'근거'}});fireEvent.click(screen.getByText('확인 기록 저장'));expect(screen.getByRole('alert').textContent).toContain('기간·투입률');expect(save).not.toHaveBeenCalled();fireEvent.change(screen.getByLabelText('시작일'),{target:{value:'2027-02-01'}});fireEvent.change(screen.getByLabelText('종료일'),{target:{value:'2027-01-01'}});fireEvent.change(screen.getByLabelText('투입률'),{target:{value:'50'}});fireEvent.click(screen.getByText('확인 기록 저장'));expect(screen.getByRole('alert').textContent).toContain('종료일');});

test('실제 업무와 근거를 따로 저장하고 취소로 확인 상태를 초기화한다',()=>{const save=vi.fn();render(<EvidenceForm disabled={false} competencies={[]} onSave={save} />);fireEvent.change(screen.getByLabelText('실제 수행 업무'),{target:{value:'해상 설계'}});fireEvent.change(screen.getByLabelText('확인 상태'),{target:{value:'CONFIRMED'}});fireEvent.change(screen.getByLabelText('확인 근거'),{target:{value:'담당 확인서'}});fireEvent.click(screen.getByText('확인 기록 저장'));expect(save).toHaveBeenCalledWith('experience',{summary:'해상 설계',basis:'담당 확인서',status:'CONFIRMED'},expect.any(Function));fireEvent.click(screen.getByText('입력 취소'));expect((screen.getByLabelText('실제 수행 업무') as HTMLTextAreaElement).value).toBe('');expect((screen.getByLabelText('확인 상태') as HTMLSelectElement).value).toBe('NEEDS_CHECK');});

test('기간·투입률·근거를 입력한 가용성을 저장한다',()=>{const save=vi.fn();render(<EvidenceForm disabled={false} competencies={[]} onSave={save} />);fireEvent.change(screen.getByLabelText('확인 종류'),{target:{value:'availability'}});fireEvent.change(screen.getByLabelText('확인 상태'),{target:{value:'CONFIRMED'}});fireEvent.change(screen.getByLabelText('시작일'),{target:{value:'2027-01-01'}});fireEvent.change(screen.getByLabelText('종료일'),{target:{value:'2027-02-01'}});fireEvent.change(screen.getByLabelText('투입률'),{target:{value:'50'}});fireEvent.change(screen.getByLabelText('확인 근거'),{target:{value:'부서 일정표'}});fireEvent.click(screen.getByText('확인 기록 저장'));expect(save).toHaveBeenCalledWith('availability',{status:'CONFIRMED',basis:'부서 일정표',period_start:'2027-01-01',period_end:'2027-02-01',allocation_rate:50},expect.any(Function));});
