import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { useQuery } from '@tanstack/react-query';
import * as browserPush from '../notifications/push';
import { me } from '../api/platform';
import { AccountPanel } from './Account';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const identity={id:'user',display_name:'합성 사용자',email:'test@example.invalid',roles:['sales'],dept:null};
const session=()=>new Response(JSON.stringify({data:identity}),{status:200,headers:{'Content-Type':'application/json','X-CSRF-Token':'session-bound-test'}});
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();clearSessionCsrfToken();setApiMode('mock');});
function view(operational=true){setApiMode('live');renderAt('/account','/account',<AccountPanel operational={operational} />);}

test('401은 고정 로그인 경로로 재로그인을 안내한다',async()=>{vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(401,errorBody('AUTH_REQUIRED','만료'))));view();const link=await screen.findByRole('link',{name:'Microsoft로 로그인'});expect(link.getAttribute('href')).toBe('/api/v1/platform/auth/login');expect(screen.getByText(/서비스 로그인과 메일 연결은 별도/)).toBeTruthy();});
test('서버 확인 실패를 로그인 만료로 바꾸지 않는다',async()=>{vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(503,errorBody('UNAVAILABLE','세션 조회 실패'))));view();await screen.findByText('세션 조회 실패');expect(screen.queryByRole('link',{name:'Microsoft로 로그인'})).toBeNull();});
test('개발 계정을 운영 로그인·메일 연결로 표시하지 않는다',async()=>{vi.stubGlobal('fetch',vi.fn(async()=>session()));view(false);await screen.findByText('합성 사용자');expect(screen.getByRole('note').textContent).toContain('시연 계정');expect(screen.queryByRole('button',{name:'로그아웃'})).toBeNull();expect(screen.queryByRole('link',{name:'Microsoft로 로그인'})).toBeNull();});
test('취소는 세션을 변경하지 않는다',async()=>{const fetchMock=vi.fn(async(_url:RequestInfo|URL)=>session());vi.stubGlobal('fetch',fetchMock);view();fireEvent.click(await screen.findByRole('button',{name:/^로그아웃$/}));fireEvent.click(screen.getByRole('button',{name:'취소'}));expect(fetchMock.mock.calls.filter(([url])=>String(url).endsWith('/logout'))).toHaveLength(0);});
test('로그아웃에 me 응답 CSRF를 사용하고 성공 후 계정을 비운다',async()=>{const fetchMock=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>String(url).endsWith('/logout')?new Response(null,{status:204}):session());vi.stubGlobal('fetch',fetchMock);view();fireEvent.click(await screen.findByRole('button',{name:/^로그아웃$/}));fireEvent.click(screen.getByRole('button',{name:'로그아웃 확인'}));await screen.findByText('로그아웃했습니다.');expect(screen.queryByText('합성 사용자')).toBeNull();const call=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/logout'))!;expect(call[1]?.method).toBe('POST');expect((call[1]?.headers as Record<string,string>)['X-CSRF-Token']).toBe('session-bound-test');});
test('로그아웃 실패는 계정을 지우지 않고 다시 시도한다',async()=>{let attempts=0;vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/logout')?(++attempts===1?jsonResponse(503,errorBody('UNAVAILABLE','철회 실패')):new Response(null,{status:204})):session()));view();fireEvent.click(await screen.findByRole('button',{name:/^로그아웃$/}));fireEvent.click(screen.getByRole('button',{name:'로그아웃 확인'}));await screen.findByText('철회 실패');expect(screen.getByText('합성 사용자')).toBeTruthy();await waitFor(()=>expect((screen.getByRole('button',{name:'로그아웃 확인'}) as HTMLButtonElement).disabled).toBe(false));fireEvent.click(screen.getByRole('button',{name:'로그아웃 확인'}));await screen.findByText('로그아웃했습니다.');});
test('CSRF 헤더가 없으면 로그아웃을 차단한다',async()=>{vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,{data:identity})));view();await screen.findByText('합성 사용자');expect((screen.getByRole('button',{name:/^로그아웃$/}) as HTMLButtonElement).disabled).toBe(true);});

test('로그아웃 대기 중 반복 클릭은 한 번만 전송한다',async()=>{
  let finish!: (response: Response) => void;
  const fetchMock=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/logout')?new Promise<Response>(resolve=>{finish=resolve;}):session());
  vi.stubGlobal('fetch',fetchMock);view();
  fireEvent.click(await screen.findByRole('button',{name:/^로그아웃$/}));
  fireEvent.click(screen.getByRole('button',{name:'로그아웃 확인'}));
  const busy=await screen.findByRole('button',{name:'로그아웃 중'});
  fireEvent.click(busy);expect((busy as HTMLButtonElement).disabled).toBe(true);
  expect(fetchMock.mock.calls.filter(([url])=>String(url).endsWith('/logout'))).toHaveLength(1);
  finish(new Response(null,{status:204}));await screen.findByText('로그아웃했습니다.');
});

function SessionObserver(){const user=useQuery({queryKey:['me'],queryFn:me});return <p data-testid="other-session-observer">{user.data?.data.display_name??'로그인 필요'}</p>;}
test('로그아웃은 공통 헤더 등 다른 세션 구독자의 계정 표시도 지운다',async()=>{
  vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/logout')?new Response(null,{status:204}):session()));
  setApiMode('live');renderAt('/account','/account',<><SessionObserver/><AccountPanel operational /></>);
  fireEvent.click(await screen.findByRole('button',{name:/^로그아웃$/}));
  fireEvent.click(screen.getByRole('button',{name:'로그아웃 확인'}));
  await screen.findByText('로그아웃했습니다.');
  expect(screen.getByTestId('other-session-observer').textContent).toBe('로그인 필요');
});

test('구독 DELETE/로컬 정리 실패는 고지하고 실제 세션 로그아웃을 계속한다', async () => {
  vi.spyOn(browserPush, 'clearPush').mockRejectedValueOnce(new Error('delete uncertain'));
  const fetcher = vi.fn(async (url: RequestInfo | URL) => String(url).endsWith('/logout') ? new Response(null, { status: 204 }) : session());
  vi.stubGlobal('fetch', fetcher); view();
  fireEvent.click(await screen.findByRole('button', { name: /^로그아웃$/ }));
  fireEvent.click(screen.getByRole('button', { name: '로그아웃 확인' }));
  await screen.findByText('로그아웃했습니다.');
  expect(screen.getByText(/OS 알림 구독 정리를 확인하지 못했습니다/)).toBeTruthy();
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/logout'))).toHaveLength(1);
});

test('세션이 없으면 계정 화면 대신 로그인 화면을 보이고 로그아웃 직후에는 안내를 함께 보인다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, errorBody('AUTH_REQUIRED', '만료'))));
  view();
  expect((await screen.findByRole('heading', { name: '로그인' })).closest('.ls-login')).not.toBeNull();
  expect(screen.queryByRole('heading', { name: '현재 계정' })).toBeNull();
  cleanup();
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).endsWith('/logout') ? new Response(null, { status: 204 }) : session()));
  view();
  fireEvent.click(await screen.findByRole('button', { name: /^로그아웃$/ }));
  fireEvent.click(screen.getByRole('button', { name: '로그아웃 확인' }));
  // 로그아웃이 끝나면 로그인 화면 안에서 로그아웃 안내와 다시 로그인 경로를 보인다.
  expect(await screen.findByText('로그아웃했습니다.')).toBeTruthy();
  expect(screen.getByRole('heading', { name: '다시 로그인' }).closest('.ls-login')).not.toBeNull();
  expect(screen.getByRole('link', { name: 'Microsoft로 로그인' }).getAttribute('href')).toBe('/api/v1/platform/auth/login');
});

test('부모가 알려 준 로그아웃 안내를 로그인 화면에서 보인다(셸 밖으로 바뀌며 다시 만들어져도 유지)', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, errorBody('AUTH_REQUIRED', '만료'))));
  setApiMode('live');
  renderAt('/account', '/account', <AccountPanel operational signedOutHint />);
  // 세션 확인이 끝나면 로그인 화면이 로그아웃 안내와 함께 나온다.
  expect((await screen.findByRole('heading', { name: '다시 로그인' })).closest('.ls-login')).not.toBeNull();
  expect(screen.getByText('로그아웃했습니다.')).toBeTruthy();
});
