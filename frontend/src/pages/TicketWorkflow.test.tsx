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
test.each([
  ['RECEIVED','접수'], ['IN_REVIEW','검토 중'], ['WAITING_CUSTOMER','고객 회신 대기'],
  ['ANSWERED','회신 완료'], ['TRANSFERRED','부서 이관'], ['CLOSED','종결'],
])('목록과 상세는 계약 상태 %s를 %s로 표시한다',async(state,label)=>{
  const fetch=vi.fn(async(url:RequestInfo|URL)=>jsonResponse(200,String(url).endsWith('/tickets')?list(state):detail(state)));
  vi.stubGlobal('fetch',fetch);const page=viewList();await screen.findByText(label,{selector:"strong"});
  expect(screen.getByText(/일반 권고/)).toBeTruthy();page.unmount();viewDetail();await screen.findByText(label,{selector:"strong"});
  expect(screen.getByText(/실행 권한이나 후속 작업의 완료를 보장하지/)).toBeTruthy();
  expect(fetch.mock.calls.every(([url])=>!String(url).includes('/workflow'))).toBe(true);
});
test.each(['NEW_STATE','toString',null,undefined])('미지원 상태 %s는 완료나 실행 권고로 추측하지 않는다',async(state)=>{
  vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,detail(state))));viewDetail();
  await screen.findByText('확인 필요');expect(screen.getByText(/현재 상태를 확인할 수 없습니다/)).toBeTruthy();
  expect(screen.queryByText('종결')).toBeNull();
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
    calls++;if(calls>1)return Promise.resolve(jsonResponse(200,list('CLOSED')));
    signal=init?.signal as AbortSignal;
    return new Promise<Response>((_resolve,reject)=>signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));
  }));
  const page=viewList();await waitFor(()=>expect(calls).toBe(1));const button=screen.getByRole('button',{name:'최신 상태 조회'});
  expect((button as HTMLButtonElement).disabled).toBe(true);fireEvent.click(button);expect(calls).toBe(1);
  page.unmount();expect(signal?.aborted).toBe(true);viewList();await screen.findByText('종결');expect(calls).toBe(2);
});
