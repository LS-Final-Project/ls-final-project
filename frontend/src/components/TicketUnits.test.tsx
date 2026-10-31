import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import TicketUnits from './TicketUnits';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
const unit = { id: 'unit-1', serial_no: 'VSN-TEST', product_code: 'P1', customer_external_id: 'C1', commissioning_date: null, commissioning_status: 'UNKNOWN', ship_date: null, fw_version_last: null, data_origin: 'synthetic', derivation_kind: 'PROVIDED' };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });
function view() { setApiMode('live'); renderAt('/', '/', <TicketUnits ticketId="ticket-1" />); }
test('사용자 조회 전 요청하지 않고 후보를 확정 이력으로 표시하지 않는다', async () => {
  const fetch = vi.fn(async (url: RequestInfo | URL) => jsonResponse(200, { data: String(url).endsWith('/units') ? {items:[{unit,link:null,matches_ticket:true}]} : {status:'NO_CONFIRMED_UNIT',unit:null,items:[]} }));
  vi.stubGlobal('fetch', fetch); view(); expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name:'장비 정보 조회'}));
  await screen.findByText('확정 장비가 없어 이전 문의를 표시하지 않습니다.');
  expect(screen.getByText('VSN-TEST')).toBeTruthy();
  // 보드의 표는 '고객/제품 일치'를 예·아니오로, 연결 상태를 후보·확정·제외로 적는다. 후보를 확정으로 적지 않는다.
  expect(screen.getByText('예')).toBeTruthy();expect(screen.getByText('후보')).toBeTruthy();expect(screen.queryByText('확정')).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(2);
});
test('교체 전 이력은 별도로 표시하고 원문 링크만 제공한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => jsonResponse(200,{data:String(url).endsWith('/units') ? {items:[]} : {status:'CONFIRMED',unit,items:[{id:'old/id',subject:'교체 전 문의',serial_no:'VSN-OLD',relation:'REPLACED_UNIT'}]}})));
  view(); fireEvent.click(screen.getByRole('button',{name:'장비 정보 조회'}));
  const link=await screen.findByRole('link',{name:'교체 전 문의'});
  expect(link.getAttribute('href')).toBe('/cs/tickets/old%2Fid');
  expect(screen.getByText(/설치·출하 정보를 현재 장비에 복사하지 않습니다/)).toBeTruthy();
});
test('갱신 권한 실패면 캐시의 장비와 이력을 모두 숨기고 재조회로 복구한다', async () => {
  let denied=false;
  vi.stubGlobal('fetch',vi.fn(async(url: RequestInfo | URL)=>denied?jsonResponse(403,errorBody('FORBIDDEN','회수')):jsonResponse(200,{data:String(url).endsWith('/units')?{items:[{unit,link:null,matches_ticket:true}]}:{status:'NO_CONFIRMED_UNIT',unit:null,items:[]}})));
  view(); fireEvent.click(screen.getByRole('button',{name:'장비 정보 조회'})); await screen.findByText('VSN-TEST');
  denied=true; fireEvent.click(screen.getByRole('button',{name:'장비 정보 새로고침'}));
  await screen.findByText('권한이 없습니다'); expect(screen.queryByText('VSN-TEST')).toBeNull();
  denied=false; fireEvent.click(screen.getByRole('button',{name:'장비 정보 새로고침'})); await screen.findByText('VSN-TEST');
});
test('조회 중 반복 클릭은 추가 요청을 만들지 않는다', async () => {
  let resolve!: () => void;
  const pending=new Promise<void>(done=>{resolve=done;});
  const fetch=vi.fn(()=>pending.then(()=>jsonResponse(503,errorBody('DEPENDENCY_UNAVAILABLE','종료')))); vi.stubGlobal('fetch',fetch); view();
  fireEvent.click(screen.getByRole('button',{name:'장비 정보 조회'}));
  const button=screen.getByRole('button',{name:'장비 정보 새로고침'});
  expect((button as HTMLButtonElement).disabled).toBe(true); fireEvent.click(button); expect(fetch).toHaveBeenCalledTimes(2);
  // 화면을 닫은 뒤 대기 중 요청이 실패해도 재전송하지 않는다.
  cleanup(); resolve();
  await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));
});

test('화면 이탈은 두 조회를 취소하고 재진입 때 이전 장비를 남기지 않는다', async () => {
  const signals: AbortSignal[]=[];
  vi.stubGlobal('fetch',vi.fn((_url: RequestInfo|URL,init?: RequestInit)=>{
    const signal=init!.signal as AbortSignal;signals.push(signal);
    return new Promise<Response>((_resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));
  }));
  view();fireEvent.click(screen.getByRole('button',{name:'장비 정보 조회'}));
  await waitFor(()=>expect(signals).toHaveLength(2));cleanup();expect(signals.every(signal=>signal.aborted)).toBe(true);
  vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>jsonResponse(200,{data:String(url).endsWith('/units')?{items:[]}:{status:'NO_CONFIRMED_UNIT',unit:null,items:[]}})));
  view();expect(screen.queryByText('VSN-TEST')).toBeNull();fireEvent.click(screen.getByRole('button',{name:'장비 정보 조회'}));
  await screen.findByText('조회 가능한 장비가 없습니다.');
});

test('같은 QueryClient의 신선한 캐시가 있어도 재진입 조회 중 이전 값을 숨긴다',async()=>{
 setApiMode('live');const client=new QueryClient({defaultOptions:{queries:{retry:false,staleTime:5000}}});
 client.setQueryData(['ticket-units','live','ticket-1'],{data:{items:[{unit,link:null,matches_ticket:true}]}});client.setQueryData(['unit-history','live','ticket-1'],{data:{status:'NO_CONFIRMED_UNIT',unit:null,items:[]}});
 vi.stubGlobal('fetch',vi.fn((_url:RequestInfo|URL,init?:RequestInit)=>new Promise<Response>((_resolve,reject)=>init?.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))))));
 const page=render(<QueryClientProvider client={client}><MemoryRouter><TicketUnits ticketId="ticket-1"/></MemoryRouter></QueryClientProvider>);
 expect(screen.queryByText('VSN-TEST')).toBeNull();fireEvent.click(screen.getByText('장비 정보 조회'));
 await waitFor(()=>expect(fetch).toHaveBeenCalled());expect(screen.queryByText('VSN-TEST')).toBeNull();page.unmount();client.clear();
});

test('고객·제품이 다른 장비는 아니오로, 확정과 제외는 연결 열에 그대로 적는다', async () => {
  const other = { ...unit, id: 'unit-2', serial_no: 'VSN-OTHER' };
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => jsonResponse(200, { data: String(url).endsWith('/units')
    ? { items: [{ unit, link: { link_status: 'CONFIRMED' }, matches_ticket: true }, { unit: other, link: { link_status: 'REJECTED' }, matches_ticket: false }] }
    : { status: 'NO_CONFIRMED_UNIT', unit: null, items: [] } })));
  view(); fireEvent.click(screen.getByRole('button', { name: '장비 정보 조회' }));
  await screen.findByText('VSN-OTHER');
  // 불일치 장비도 어느 고객 장비인지 보여야 한다.
  expect(screen.getAllByText(/고객 C1, 출처 synthetic PROVIDED/).length).toBe(2);
  expect(screen.getByText('아니오')).toBeTruthy();
  expect(screen.getByText('확정')).toBeTruthy();
  expect(screen.getByText('제외')).toBeTruthy();
});
