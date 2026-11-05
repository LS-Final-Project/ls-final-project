import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { useQuery } from '@tanstack/react-query';
import { BusinessGate, useBusinessAccess } from './BusinessAccess';
import { request, setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
function Probe({enabled=true}:{enabled?:boolean}){
 const access=useBusinessAccess('tft',true,enabled);
 const data=useQuery({queryKey:['probe-business'],queryFn:()=>request<{data:string}>('/api/v1/tft/projects'),enabled:access.ready,retry:false});
 if(!access.ready)return <BusinessGate access={access} title="프로젝트"/>;
 return <><p>{data.data?.data}</p><button onClick={()=>access.user.refetch()}>세션 재확인</button></>;
}
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');});
function render(enabled=true){setApiMode('live');renderAt('/','/',<Probe enabled={enabled}/>);}
test('운영 기능 꺼짐은 인증·업무 요청 없이 준비 중이다',async()=>{const f=vi.fn();vi.stubGlobal('fetch',f);render(false);await screen.findByText('업무 화면은 준비 중입니다.');expect(f).not.toHaveBeenCalled();});
test('세션 응답 전에는 업무 API를 호출하지 않는다',async()=>{let finish!:(r:Response)=>void;const f=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/me')?new Promise<Response>(resolve=>{finish=resolve;}):jsonResponse(200,{data:'허용 프로젝트'}));vi.stubGlobal('fetch',f);render();await screen.findByText('접근 권한 확인 중');expect(f.mock.calls).toHaveLength(1);finish(jsonResponse(200,{data:{roles:['sales']}}));await screen.findByText('허용 프로젝트');});
test('미로그인은 계정 안내만 표시하고 업무 조회를 막는다',async()=>{const f=vi.fn(async()=>jsonResponse(401,errorBody('AUTH_REQUIRED','만료')));vi.stubGlobal('fetch',f);render();await screen.findByRole('link',{name:'계정 확인'});expect(f.mock.calls).toHaveLength(1);});
test('업무 역할이 없으면 조회 전에 거절한다',async()=>{const f=vi.fn(async()=>jsonResponse(200,{data:{roles:['cs_agent']}}));vi.stubGlobal('fetch',f);render();await screen.findByText('권한이 없습니다');expect(f.mock.calls).toHaveLength(1);});
test('세션 확인 실패를 빈 목록으로 표시하지 않는다',async()=>{vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(503,errorBody('UNAVAILABLE','세션 연결 실패'))));render();await screen.findByText('세션 연결 실패');expect(screen.getByRole('button',{name:'다시 시도'})).toBeTruthy();});
test('세션 회수 후 캐시에 남은 업무 내용을 숨긴다',async()=>{let calls=0;vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/me')?(++calls===1?jsonResponse(200,{data:{roles:['sales']}}):jsonResponse(401,errorBody('AUTH_REQUIRED','만료'))):jsonResponse(200,{data:'이전 업무 내용'})));render();await screen.findByText('이전 업무 내용');fireEvent.click(screen.getByRole('button',{name:'세션 재확인'}));await screen.findByRole('link',{name:'계정 확인'});await waitFor(()=>expect(screen.queryByText('이전 업무 내용')).toBeNull());});
