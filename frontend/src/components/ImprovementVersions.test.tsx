import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import ImprovementVersions from './ImprovementVersions';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
const body={data:{items:[{version:1,changed_by:null,changed_at:'2026-10-27T00:00:00Z',content:{problem_statement:'합성 판본 문제',condition_status:'UNKNOWN',evidence:[{ticket_id:'TEST/ONE',case_id:null}],proposed_depts:[]}}]}};
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');});
function view(){setApiMode('live');return renderAt('/','/',<ImprovementVersions requestId="test/request"/>);}
test('명시적으로 연 뒤에만 판본을 읽고 원문 링크와 미기록을 구분한다',async()=>{
 const fetch=vi.fn(async()=>jsonResponse(200,body));vi.stubGlobal('fetch',fetch);view();expect(fetch).not.toHaveBeenCalled();fireEvent.click(screen.getByText('판본 이력 조회'));await screen.findByText('판본 1');expect(screen.getByRole('link',{name:'TEST/ONE'}).getAttribute('href')).toBe('/cs/tickets/TEST%2FONE');expect(screen.getByText(/검토 승인·전달 완료를 뜻하지/)).toBeTruthy();expect(fetch.mock.calls[0]).toBeTruthy();
});
test.each([401,403,404,503])('판본 갱신 %i는 이전 내용을 숨기고 복구한다',async(status)=>{
 let fail=false;vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(fail?status:200,fail?errorBody('FAIL','합성 오류'):body)));view();fireEvent.click(screen.getByText('판본 이력 조회'));await screen.findByText('합성 판본 문제');fail=true;fireEvent.click(screen.getByText('판본 이력 새로고침'));await screen.findByRole('alert');expect(screen.queryByText('합성 판본 문제')).toBeNull();fail=false;fireEvent.click(screen.getByText('판본 이력 새로고침'));await screen.findByText('합성 판본 문제');
});
test('빈 판본 목록은 저장된 판본 없음으로 표시한다',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,{data:{items:[]}})));view();fireEvent.click(screen.getByText('판본 이력 조회'));await screen.findByText('저장된 판본이 없습니다.');
});
test('구조가 다른 snapshot은 내용을 추측하거나 외부 링크로 렌더링하지 않는다',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,{data:{items:[{version:2,changed_at:'2026-10-27',changed_by:null,content:{problem_statement:{html:'<script>'},evidence:[null,{ticket_id:3}],proposed_depts:'unexpected'}}]}})));view();fireEvent.click(screen.getByText('판본 이력 조회'));await screen.findByText('판본 2');expect(screen.getAllByText('근거 형식 확인 필요')).toHaveLength(2);expect(screen.queryByRole('link')).toBeNull();
});
test('중복 조회 방지와 이탈 취소 후 재진입 시 새로 조회한다',async()=>{
 let signal:AbortSignal|undefined;let calls=0;vi.stubGlobal('fetch',vi.fn((_url:RequestInfo|URL,init?:RequestInit)=>{calls++;if(calls>1)return Promise.resolve(jsonResponse(200,body));signal=init?.signal as AbortSignal;return new Promise<Response>((_r,reject)=>signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));}));const page=view();fireEvent.click(screen.getByText('판본 이력 조회'));await waitFor(()=>expect(calls).toBe(1));fireEvent.click(screen.getByText('판본 이력 새로고침'));expect(calls).toBe(1);page.unmount();expect(signal?.aborted).toBe(true);view();fireEvent.click(screen.getByText('판본 이력 조회'));await screen.findByText('합성 판본 문제');expect(calls).toBe(2);
});

test('같은 QueryClient의 신선한 캐시가 있어도 재진입 조회 중 이전 값을 숨긴다',async()=>{
 setApiMode('live');const client=new QueryClient({defaultOptions:{queries:{retry:false,staleTime:5000}}});
 client.setQueryData(['cs-improvement-versions','live','test/request'],body);
 vi.stubGlobal('fetch',vi.fn((_url:RequestInfo|URL,init?:RequestInit)=>new Promise<Response>((_resolve,reject)=>init?.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))))));
 const page=render(<QueryClientProvider client={client}><MemoryRouter><ImprovementVersions requestId="test/request"/></MemoryRouter></QueryClientProvider>);
 expect(screen.queryByText('합성 판본 문제')).toBeNull();fireEvent.click(screen.getByText('판본 이력 조회'));
 await waitFor(()=>expect(fetch).toHaveBeenCalled());expect(screen.queryByText('합성 판본 문제')).toBeNull();page.unmount();client.clear();
});
