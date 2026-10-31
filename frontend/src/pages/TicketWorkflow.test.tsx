import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import Tickets from './Tickets';
import TicketDetail from './TicketDetail';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';
const source = examples.find(x => x.path === '/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222')!;
const base = (source.body as {data: Record<string, unknown>}).data;
const detail = (state: unknown) => ({data: {...base, workflow_state: state}});
const list = (state: unknown) => ({data: {items: [{...base, workflow_state: state}], next_cursor: null}});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });
function viewList(){setApiMode('live');return renderAt('/cs/tickets','/cs/tickets',<Tickets/>);}
function viewDetail(){setApiMode('live');return renderAt('/cs/tickets/x','/cs/tickets/:ticketId',<TicketDetail/>);}
// 목록은 보드의 문구(검토중, 답변 완료, 부서 검토 중), 상세는 상태 안내 문구를 쓴다.
const isRequests = (url: RequestInfo | URL) => String(url).includes('/improvement-requests');
test.each([
  ['RECEIVED','접수','접수'], ['IN_REVIEW','검토 중','검토중'], ['WAITING_CUSTOMER','고객 회신 대기','고객 회신 대기'],
  ['ANSWERED','회신 완료','답변 완료'], ['TRANSFERRED','부서 이관','부서 검토 중'], ['CLOSED','종결','종결'],
])('목록과 상세는 계약 상태 %s를 상세 %s, 목록 %s로 표시한다',async(state,label,listLabel)=>{
  // 업무 흐름 조회(/workflow)가 다른 상태를 돌려줘도 화면의 상태 표시는 문의 상세·목록의 값을 따른다.
  const other=state==='CLOSED'?'RECEIVED':'CLOSED';
  const flow={data:{ticket_id:'x',state:other,version:7,owner_user_id:null,closed_at:null,closed_by:null,customer_sent_at:null,dept_results:[],transfers:[]}};
  const fetch=vi.fn(async(url:RequestInfo|URL)=>jsonResponse(200,isRequests(url)?{data:{items:[]}}:String(url).endsWith('/workflow')?flow:String(url).endsWith('/tickets')?list(state):detail(state)));
  vi.stubGlobal('fetch',fetch);const page=viewList();
  // 목록의 상태 조건은 기본으로 종결을 뺀다. 종결 시험은 전체를 고른다.
  if(state==='CLOSED'){await screen.findByRole('table',{name:'문의 목록'});fireEvent.change(screen.getByLabelText('상태'),{target:{value:''}});}
  await screen.findByText(listLabel,{selector:'td'});
  page.unmount();viewDetail();await screen.findByText(label,{selector:"strong"});
  expect(screen.getByText(/실행 권한이나 후속 작업의 완료를 보장하지/)).toBeTruthy();
  expect(screen.queryByText(other==='CLOSED'?'종결':'접수',{selector:'strong'})).toBeNull();
  await waitFor(()=>expect(fetch.mock.calls.some(([url])=>String(url).endsWith('/workflow'))).toBe(true));
});
test.each(['NEW_STATE','toString',null,undefined])('미지원 상태 %s는 완료나 실행 권고로 추측하지 않는다',async(state)=>{
  vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,detail(state))));viewDetail();
  await screen.findByText('확인 필요');expect(screen.getByText(/현재 상태를 확인할 수 없습니다/)).toBeTruthy();
  // 기타 메뉴의 '종결' 동작 이름과 구분하려고 상태 표시(strong)만 본다.
  expect(screen.queryByText('종결',{selector:'strong'})).toBeNull();
});
test.each([401,403,503])('상세 갱신 %i 실패는 옛 상태를 숨기고 재조회로 복구한다',async(status)=>{
  let fail=false;vi.stubGlobal('fetch',vi.fn(async()=>fail?jsonResponse(status,errorBody('FAILED','합성 오류')):jsonResponse(200,detail('IN_REVIEW'))));
  viewDetail();await screen.findByText('검토 중');fail=true;fireEvent.click(screen.getByRole('button',{name:'최신 상태 조회'}));
  await waitFor(()=>expect(screen.queryByText('검토 중')).toBeNull());
  fail=false;fireEvent.click(screen.getByRole('button',{name:'최신 상태 조회'}));await screen.findByText('검토 중');
});
test('조회 중 반복 클릭을 막고 이탈 시 취소하며 재진입은 새 상태를 조회한다',async()=>{
  let signal:AbortSignal|undefined;let calls=0;
  vi.stubGlobal('fetch',vi.fn((_url:RequestInfo|URL,init?:RequestInit)=>{
    if(isRequests(_url))return Promise.resolve(jsonResponse(200,{data:{items:[]}}));
    calls++;if(calls>1)return Promise.resolve(jsonResponse(200,list('IN_REVIEW')));
    signal=init?.signal as AbortSignal;
    return new Promise<Response>((_resolve,reject)=>signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));
  }));
  const page=viewList();await waitFor(()=>expect(calls).toBe(1));const button=screen.getByRole('button',{name:/^조회/});
  expect((button as HTMLButtonElement).disabled).toBe(true);fireEvent.click(button);expect(calls).toBe(1);
  page.unmount();expect(signal?.aborted).toBe(true);viewList();await screen.findByText('검토중',{selector:'td'});expect(calls).toBe(2);
});
