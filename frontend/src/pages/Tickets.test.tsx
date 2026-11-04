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
// 관리자 단추(담당 변경·자동 배정)용 세션 확인(/platform/me)도 따로 답한다. 기본은 C/S 담당자다.
const isMe=(url:RequestInfo|URL)=>String(url).endsWith('/platform/me');
const AGENT_ME={id:'c',roles:['cs_agent'],managed_departments:[]};
function stub(tickets:(url:string)=>Response,me:object=AGENT_ME){const f=vi.fn(async(url:RequestInfo|URL)=>isRequests(url)?jsonResponse(200,{data:{items:[]}}):isMe(url)?jsonResponse(200,{data:me}):tickets(String(url)));vi.stubGlobal('fetch',f);return f;}
const ticketCalls=(f:ReturnType<typeof stub>)=>f.mock.calls.filter(([url])=>!isRequests(url as RequestInfo|URL)&&!isMe(url as RequestInfo|URL));
const grid=()=>within(screen.getByRole('table',{name:'문의 목록'}));
const subjects=()=>grid().getAllByRole('link').map(x=>x.textContent);
const only=(list:unknown[])=>jsonResponse(200,{data:{items:list,next_cursor:null}});

test('조회조건은 받아 온 목록 안에서만 거르고 서버 전체 검색으로 표시하지 않는다',async()=>{stub(()=>only(items));view();await screen.findByRole('link',{name:'Alpha'});expect(screen.queryByText(/서버 필터, 요약, 고객 열 아직 없음/)).toBeNull();expect(screen.queryByText('다음 할 일')).toBeNull();expect(screen.queryByRole('grid',{name:'개선요청 목록'})).toBeNull();fireEvent.change(screen.getByLabelText('검색어'),{target:{value:'ID-B'}});expect(subjects()).toEqual(['Beta']);fireEvent.change(screen.getByLabelText('접수 경로'),{target:{value:'EMAIL'}});expect(screen.getByText(/조건에 맞는 문의가 없습니다/)).toBeTruthy();fireEvent.click(screen.getByRole('button',{name:/조건 지우기/}));expect(subjects()).toEqual(['Alpha','Beta']);});
test('상태 조건은 기본으로 종결을 빼고 전체를 고르면 종결도 보여 준다',async()=>{stub(()=>only([...items,{...items[0],id:'c',subject:'Closed',external_id:'ID-C',workflow_state:'CLOSED'}]));view();await screen.findByRole('link',{name:'Alpha'});expect(screen.queryByRole('link',{name:'Closed'})).toBeNull();fireEvent.change(screen.getByLabelText('상태'),{target:{value:''}});expect(screen.getByRole('link',{name:'Closed'})).toBeTruthy();fireEvent.change(screen.getByLabelText('상태'),{target:{value:'CLOSED'}});expect(subjects()).toEqual(['Closed']);});
test('접수일자 범위는 받아 온 목록에 적용한다',async()=>{stub(()=>only(items));view();await screen.findByRole('link',{name:'Alpha'});fireEvent.change(screen.getByLabelText('접수일자 시작'),{target:{value:'2026-10-21'}});expect(subjects()).toEqual(['Beta']);fireEvent.change(screen.getByLabelText('접수일자 종료'),{target:{value:'2026-10-20'}});expect(screen.getByText(/조건에 맞는 문의가 없습니다/)).toBeTruthy();});
test('기본은 경과일 긴 순이고 접수일시 머리를 누르면 최근 접수 순으로 바꾼다',async()=>{stub(()=>only(items));view();await screen.findByRole('link',{name:'Alpha'});expect(subjects()).toEqual(['Alpha','Beta']);expect(screen.getByRole('button',{name:/경과일 내림차순/})).toBeTruthy();fireEvent.click(screen.getByRole('button',{name:/접수일시 정렬/}));expect(subjects()).toEqual(['Beta','Alpha']);expect(screen.getByRole('button',{name:/접수일시 내림차순/})).toBeTruthy();});
test('마지막 쪽의 다음은 다음 커서를 인코딩해 보내고 받은 행을 이어 붙인다',async()=>{const f=stub(url=>jsonResponse(200,{data:url.includes('cursor=')?{items:[{...items[0],id:'n',subject:'다음 문의',external_id:'ID-N'}],next_cursor:null}:{items,next_cursor:'abc+/='}}));view();await screen.findByRole('link',{name:'Alpha'});fireEvent.click(screen.getByRole('button',{name:'다음'}));await screen.findByRole('link',{name:'다음 문의'});expect(ticketCalls(f).some(([url])=>String(url).includes('cursor=abc%2B%2F%3D'))).toBe(true);expect(subjects()).toContain('Alpha');expect(screen.getByText('총 3건 중 3건')).toBeTruthy();expect((screen.getByRole('button',{name:'다음'}) as HTMLButtonElement).disabled).toBe(true);});
test('갱신 권한 거부는 이전 데이터를 숨기고 다시 조회로 복구한다',async()=>{let calls=0;stub(()=>++calls===2?jsonResponse(403,errorBody('FORBIDDEN','회수')):only(items));view();await screen.findByRole('link',{name:'Alpha'});fireEvent.click(screen.getByRole('button',{name:/^조회/}));await screen.findByText('권한이 없습니다');await waitFor(()=>expect(screen.queryByRole('link',{name:'Alpha'})).toBeNull());fireEvent.click(screen.getByRole('button',{name:/^조회/}));await screen.findByRole('link',{name:'Alpha'});});
test('원문과 언어를 표시하고 AI 분석을 만들어내지 않는다',async()=>{const source=examples.find(x=>x.path==='/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222')!;const text=(source.body as {data:{body:string}}).data.body;vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,source.body)));setApiMode('live');renderAt('/cs/tickets/x','/cs/tickets/:ticketId',<TicketDetail/>);await screen.findByRole('heading',{name:'문의 처리'});await waitFor(()=>expect(document.querySelector('.td3 .mail')?.textContent).toBe(text));expect(screen.getByText(/파트너 포털, 튀르키예어/)).toBeTruthy();expect(screen.getByText(/아직 AI 분석이 없습니다/)).toBeTruthy();expect(screen.queryByText('AI',{selector:'.box .ai2'})).toBeNull();});

test('제품 조건은 접수 경로와 결합하고 다음 묶음을 받은 뒤에도 유지하며 지우면 풀린다',async()=>{
  const f=stub(url=>jsonResponse(200,{data:url.includes('cursor=')?{items:[{...items[0],id:'c',subject:'Gamma',external_id:'ID-G'}],next_cursor:null}:{items,next_cursor:'next'}}));
  view();await screen.findByRole('link',{name:'Alpha'});
  fireEvent.change(screen.getByLabelText('제품'),{target:{value:'P2'}});
  expect(subjects()).toEqual(['Beta']);
  fireEvent.change(screen.getByLabelText('접수 경로'),{target:{value:'EMAIL'}});
  expect(screen.getByText(/조건에 맞는 문의가 없습니다/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('접수 경로'),{target:{value:''}});
  fireEvent.click(screen.getByRole('button',{name:'다음'}));
  await waitFor(()=>expect((screen.getByRole('button',{name:'다음'}) as HTMLButtonElement).disabled).toBe(true));
  expect((screen.getByLabelText('제품') as HTMLSelectElement).value).toBe('P2');
  expect(screen.queryByRole('link',{name:'Gamma'})).toBeNull();
  expect(ticketCalls(f).length).toBe(2);
  fireEvent.click(screen.getByRole('button',{name:/조건 지우기/}));
  expect(subjects()).toEqual(['Alpha','Gamma','Beta']);
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

// 선택 바의 담당 변경은 서버와 같은 조건(manager 역할 + C/S 부서 GCS_L1 관리)일 때만 열린다.
for(const [who,data,open] of [['C/S 부서 관리자',{id:'m',roles:['manager'],managed_departments:['GCS_L1']},true],['다른 부서 관리자',{id:'m',roles:['manager'],managed_departments:['TS_TRF']},false],['C/S 담당자',{id:'c',roles:['cs_agent'],managed_departments:[]},false]] as const){
  test(`선택 바 담당 변경: ${who}이면 ${open?'열린다':'잠긴다'}`,async()=>{
    stub(()=>only(items),data);view();
    await screen.findByRole('link',{name:'Alpha'});
    expect(screen.queryByRole('toolbar',{name:'선택한 문의'})).toBeNull();
    fireEvent.click(screen.getByLabelText('ID-A 선택'));
    const bar=within(screen.getByRole('toolbar',{name:'선택한 문의'}));
    expect(screen.getByRole('toolbar',{name:'선택한 문의'}).textContent).toContain('1건 선택');
    await waitFor(()=>expect((bar.getByRole('button',{name:'담당 변경'}) as HTMLButtonElement).disabled).toBe(!open));
    if(open){fireEvent.click(bar.getByRole('button',{name:'담당 변경'}));expect(await screen.findByText('담당 변경, 1건')).toBeTruthy();}
    else await waitFor(()=>expect(bar.getByRole('button',{name:'담당 변경'}).getAttribute('title')).toBe('C/S 부서 관리자만 담당을 바꿀 수 있습니다'));
    fireEvent.click(bar.getByRole('button',{name:'선택 해제'}));
    if(!open) expect(screen.queryByRole('toolbar',{name:'선택한 문의'})).toBeNull();
  });
}

test('문의 목록은 한 쪽에 10건씩 보이고 쪽 번호로 넘긴다',async()=>{
  const many=Array.from({length:12},(_,i)=>({...items[0],id:`m${i}`,subject:`문의 ${String(i).padStart(2,'0')}`,external_id:`ID-${i}`,received_at:`2026-10-${String(i+1).padStart(2,'0')}T12:00:00Z`}));
  stub(()=>only(many));view();await screen.findByRole('link',{name:'문의 00'});
  expect(subjects()).toHaveLength(10);expect(subjects()[0]).toBe('문의 00');
  fireEvent.click(screen.getByRole('button',{name:'2쪽'}));expect(subjects()).toEqual(['문의 10','문의 11']);
});
test('한국어 번역 제목이 있으면 목록에 보이고 원문은 툴팁으로 남긴다, 검색은 둘 다 찾는다',async()=>{
  stub(()=>only([{...items[0],subject_ko:'알파 고장'},items[1]]));view();
  const link=await screen.findByRole('link',{name:'알파 고장'});
  expect(link.getAttribute('title')).toBe('원문: Alpha');expect(subjects()).toEqual(['알파 고장','Beta']);
  fireEvent.change(screen.getByLabelText('검색어'),{target:{value:'Alpha'}});expect(subjects()).toEqual(['알파 고장']);
  fireEvent.change(screen.getByLabelText('검색어'),{target:{value:'고장'}});expect(subjects()).toEqual(['알파 고장']);
});
