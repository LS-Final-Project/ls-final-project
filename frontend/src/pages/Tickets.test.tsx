import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import Tickets from './Tickets';
import TicketDetail from './TicketDetail';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';
const items=[
  {id:'a',subject:'Alpha',external_id:'ID-A',channel:'EMAIL',product_code:'P1',received_at:'2026-10-20T12:00:00Z',data_origin:'synthetic',workflow_state:'IN_REVIEW'},
  {id:'b',subject:'Beta',external_id:'ID-B',channel:'PHONE',product_code:'P2',received_at:'2026-10-21T12:00:00Z',data_origin:'synthetic',workflow_state:'RECEIVED'},
];
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');});
function view(){setApiMode('live');renderAt('/cs/tickets','/cs/tickets',<Tickets/>);}
// 개선요청 목록(화면 아래 표)은 문의 목록과 따로 불린다. 문의 목록 호출만 시험 대상으로 삼는다.
const isRequests=(url:RequestInfo|URL)=>String(url).includes('/improvement-requests');
function stub(tickets:(url:string)=>Response){const f=vi.fn(async(url:RequestInfo|URL)=>isRequests(url)?jsonResponse(200,{data:{items:[]}}):tickets(String(url)));vi.stubGlobal('fetch',f);return f;}
const ticketCalls=(f:ReturnType<typeof stub>)=>f.mock.calls.filter(([url])=>!isRequests(url as RequestInfo|URL));
const grid=()=>within(screen.getByRole('table',{name:'문의 목록'}));
const subjects=()=>grid().getAllByRole('link').map(x=>x.textContent);
const only=(list:unknown[])=>jsonResponse(200,{data:{items:list,next_cursor:null}});

test('조회조건은 받아 온 목록 안에서만 거르고 서버 전체 검색으로 표시하지 않는다',async()=>{stub(()=>only(items));view();await screen.findByRole('link',{name:'Alpha'});expect(screen.getByText(/조회조건은 받아 온 목록 안에서 거름/)).toBeTruthy();expect(screen.getByText(/서버 필터, 요약, 고객 열 아직 없음/)).toBeTruthy();fireEvent.change(screen.getByLabelText('검색어'),{target:{value:'ID-B'}});expect(subjects()).toEqual(['Beta']);fireEvent.change(screen.getByLabelText('접수 경로'),{target:{value:'EMAIL'}});expect(screen.getByText(/조건에 맞는 문의가 없습니다/)).toBeTruthy();fireEvent.click(screen.getByRole('link',{name:'조건 지우기'}));expect(subjects()).toEqual(['Beta','Alpha']);});
test('상태 조건은 기본으로 종결을 빼고 전체를 고르면 종결도 보여 준다',async()=>{stub(()=>only([...items,{...items[0],id:'c',subject:'Closed',external_id:'ID-C',workflow_state:'CLOSED'}]));view();await screen.findByRole('link',{name:'Alpha'});expect(screen.queryByRole('link',{name:'Closed'})).toBeNull();fireEvent.change(screen.getByLabelText('상태'),{target:{value:''}});expect(screen.getByRole('link',{name:'Closed'})).toBeTruthy();fireEvent.change(screen.getByLabelText('상태'),{target:{value:'CLOSED'}});expect(subjects()).toEqual(['Closed']);});
test('접수일자 범위는 받아 온 목록에 적용한다',async()=>{stub(()=>only(items));view();await screen.findByRole('link',{name:'Alpha'});fireEvent.change(screen.getByLabelText('접수일자 시작'),{target:{value:'2026-10-21'}});expect(subjects()).toEqual(['Beta']);fireEvent.change(screen.getByLabelText('접수일자 종료'),{target:{value:'2026-10-20'}});expect(screen.getByText(/조건에 맞는 문의가 없습니다/)).toBeTruthy();});
test('접수일시 정렬은 현재 받은 목록에 적용한다',async()=>{stub(()=>only(items));view();await screen.findByRole('link',{name:'Alpha'});expect(subjects()).toEqual(['Beta','Alpha']);fireEvent.click(screen.getByRole('button',{name:/접수일시 내림차순/}));expect(subjects()).toEqual(['Alpha','Beta']);expect(screen.getByRole('button',{name:/접수일시 오름차순/})).toBeTruthy();});
test('더 보기는 다음 커서를 인코딩해 보내고 받은 행을 이어 붙인다',async()=>{const f=stub(url=>jsonResponse(200,{data:url.includes('cursor=')?{items:[{...items[0],id:'n',subject:'다음 문의',external_id:'ID-N'}],next_cursor:null}:{items,next_cursor:'abc+/='}}));view();await screen.findByRole('link',{name:'Alpha'});fireEvent.click(screen.getByRole('button',{name:'50건 더 보기'}));await screen.findByRole('link',{name:'다음 문의'});expect(ticketCalls(f).some(([url])=>String(url).includes('cursor=abc%2B%2F%3D'))).toBe(true);expect(subjects()).toContain('Alpha');expect(screen.getByText('표시 3건 / 조회 3건')).toBeTruthy();expect((screen.getByRole('button',{name:'모두 불러옴'}) as HTMLButtonElement).disabled).toBe(true);});
test('갱신 권한 거부는 이전 데이터를 숨기고 다시 조회로 복구한다',async()=>{let calls=0;stub(()=>++calls===2?jsonResponse(403,errorBody('FORBIDDEN','회수')):only(items));view();await screen.findByRole('link',{name:'Alpha'});fireEvent.click(screen.getByRole('button',{name:/^조회/}));await screen.findByText('권한이 없습니다');await waitFor(()=>expect(screen.queryByRole('link',{name:'Alpha'})).toBeNull());fireEvent.click(screen.getByRole('button',{name:/^조회/}));await screen.findByRole('link',{name:'Alpha'});});
test('원문과 언어를 표시하고 AI 분석을 만들어내지 않는다',async()=>{const source=examples.find(x=>x.path==='/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222')!;const text=(source.body as {data:{body:string}}).data.body;vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,source.body)));setApiMode('live');renderAt('/cs/tickets/x','/cs/tickets/:ticketId',<TicketDetail/>);await screen.findByRole('heading',{name:'문의 처리'});await waitFor(()=>expect(document.querySelector('.kv-text')?.textContent).toBe(text));expect(screen.getByText(/파트너 포털, tr/)).toBeTruthy();expect(screen.getByText(/분석 결과를 확인할 수 없습니다/)).toBeTruthy();expect((screen.getByLabelText('주분류') as HTMLSelectElement).disabled).toBe(true);});

test('제품 조건은 접수 경로와 결합하고 더 보기 뒤에도 유지하며 지우면 풀린다',async()=>{
  const f=stub(url=>jsonResponse(200,{data:url.includes('cursor=')?{items:[{...items[0],id:'c',subject:'Gamma',external_id:'ID-G'}],next_cursor:null}:{items,next_cursor:'next'}}));
  view();await screen.findByRole('link',{name:'Alpha'});
  fireEvent.change(screen.getByLabelText('제품'),{target:{value:'P2'}});
  expect(subjects()).toEqual(['Beta']);
  fireEvent.change(screen.getByLabelText('접수 경로'),{target:{value:'EMAIL'}});
  expect(screen.getByText(/조건에 맞는 문의가 없습니다/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('접수 경로'),{target:{value:''}});
  fireEvent.click(screen.getByRole('button',{name:'50건 더 보기'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'모두 불러옴'})).toBeTruthy());
  expect((screen.getByLabelText('제품') as HTMLSelectElement).value).toBe('P2');
  expect(screen.queryByRole('link',{name:'Gamma'})).toBeNull();
  expect(ticketCalls(f).length).toBe(2);
  fireEvent.click(screen.getByRole('link',{name:'조건 지우기'}));
  expect(subjects()).toEqual(['Beta','Alpha','Gamma']);
  expect((screen.getByLabelText('제품') as HTMLSelectElement).value).toBe('');
});

const detail=(note:string|null,fails=false)=>{const source=examples.find(x=>x.path==='/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222')!;vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/analysis')?(fails?jsonResponse(500,errorBody('X','실패')):jsonResponse(200,{data:{rules:{tier_note:note}}})):jsonResponse(200,source.body)));setApiMode('live');renderAt('/cs/tickets/a','/cs/tickets/:ticketId',<TicketDetail/>);return source;};
test('등급을 몰라 표준으로 계산했으면 문의 화면에 알린다',async()=>{detail('등급 미확인, 표준 기준 계산');expect((await screen.findByRole('note')).textContent).toBe('등급 미확인, 표준 기준 계산');});
test('등급이 있으면 안내가 없고 분석을 못 읽어도 문의는 보인다',async()=>{const source=detail(null);await screen.findByText('문의번호');await waitFor(()=>expect(vi.mocked(fetch).mock.calls.some(([u])=>String(u).endsWith('/analysis'))).toBe(true));expect(screen.queryByRole('note')).toBeNull();cleanup();detail(null,true);await screen.findByText('문의번호');expect(screen.queryByRole('alert')).toBeNull();expect(source).toBeTruthy();});
test('엑셀은 선택한 행이 조건으로 모두 가려지면 보이는 행 전체를 내려받는다',async()=>{
  stub(()=>only(items));view();await screen.findByRole('link',{name:'Alpha'});
  let blob:Blob|undefined;
  vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:(value:Blob)=>{blob=value;return 'blob:test';},revokeObjectURL:()=>{}}));
  const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
  fireEvent.click(screen.getByLabelText('ID-A 선택'));
  expect(screen.getByRole('button',{name:/^엑셀/}).getAttribute('title')).toBe('선택한 1건만 내려받습니다');
  // 선택한 Alpha(P1)가 제품 조건으로 가려져도 Beta만 보이므로 빈 파일이 아니라 보이는 행을 내려받는다.
  fireEvent.change(screen.getByLabelText('제품'),{target:{value:'P2'}});
  expect(screen.getByRole('button',{name:/^엑셀/}).getAttribute('title')).toBe('표시된 행 전체를 내려받습니다');
  fireEvent.click(screen.getByRole('button',{name:/^엑셀/}));
  const text=await blob!.text();
  expect(text).toContain('ID-B');expect(text).not.toContain('ID-A');
  click.mockRestore();
});
