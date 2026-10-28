import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import ImprovementEditor from './ImprovementEditor';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import type { ImprovementDetail } from '../api/cs-improvements';
import examples from '../mocks/generated/cs.json';
import { errorBody, jsonResponse, renderAt } from '../test/render';
const fixture=examples.find(x=>x.path.includes('/improvement-requests/'))!.body as unknown as ImprovementDetail;
const base=()=>{const data=structuredClone(fixture);data.data.request.problem_statement='합성 검토 문제';return data;};
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');clearSessionCsrfToken();});
function setup(status=200,role='cs_agent'){
 const saved=vi.fn();const reload=vi.fn();const data=base();const next=base();next.data.request.version=2;next.data.request.problem_statement='수정한 문제';
 const fetch=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:[role]}}),{status:200,headers:{'X-CSRF-Token':'synthetic-csrf'}}):jsonResponse(status,status===200?next:errorBody('FAIL','합성 저장 실패')));
 vi.stubGlobal('fetch',fetch);setApiMode('live');renderAt('/','/',<ImprovementEditor record={data.data.request} onSaved={saved} onReload={reload}/>);return {fetch,saved,reload,next};
}
function edit(){fireEvent.click(screen.getByText('내용 수정'));fireEvent.change(screen.getByLabelText('검토할 문제'),{target:{value:'수정한 문제'}});}
test('판본과 변경한 내용만 보내고 서버 응답을 상세 갱신에 사용한다',async()=>{
 const {fetch,saved,next}=setup();edit();fireEvent.click(screen.getByText('내용 저장'));await waitFor(()=>expect(saved).toHaveBeenCalledWith(next));
 const init=(fetch.mock.calls as unknown as [string,RequestInit][])[1][1];expect(init.method).toBe('PATCH');expect(JSON.parse(init.body as string)).toEqual({version:1,problem_statement:'수정한 문제'});expect(init.headers).toMatchObject({'X-CSRF-Token':'synthetic-csrf'});
});
test.each([409,503])('저장 %i는 자동 재전송 없이 최신 내용 확인을 요구한다',async(status)=>{
 const {fetch,saved,reload}=setup(status);edit();fireEvent.click(screen.getByText('내용 저장'));await screen.findByRole('alert');expect(saved).not.toHaveBeenCalled();expect((screen.getByText('내용 저장') as HTMLButtonElement).disabled).toBe(true);fireEvent.click(screen.getByText('내용 저장'));expect(fetch).toHaveBeenCalledTimes(2);fireEvent.click(screen.getByText('입력 버리고 최신 내용 조회'));expect(reload).toHaveBeenCalledTimes(1);
});
test.each(['manager','dept_staff'])('권한 %s는 PATCH 전에 거부한다',async(role)=>{
 const {fetch,saved}=setup(200,role);edit();fireEvent.click(screen.getByText('내용 저장'));await screen.findByRole('alert');expect(fetch).toHaveBeenCalledTimes(1);expect(saved).not.toHaveBeenCalled();
});
test('변경 없는 저장과 공백 문제는 차단하고 취소 시 입력을 버린다',()=>{
 const {fetch}=setup();fireEvent.click(screen.getByText('내용 수정'));expect((screen.getByText('내용 저장') as HTMLButtonElement).disabled).toBe(true);fireEvent.change(screen.getByLabelText('검토할 문제'),{target:{value:'   '}});expect((screen.getByText('내용 저장') as HTMLButtonElement).disabled).toBe(true);fireEvent.click(screen.getByText('수정 취소'));fireEvent.click(screen.getByText('내용 수정'));expect((screen.getByLabelText('검토할 문제') as HTMLTextAreaElement).value).toBe('합성 검토 문제');expect(fetch).not.toHaveBeenCalled();
});
test('저장 중 반복 클릭은 하나의 PATCH이며 이탈은 요청을 취소한다',async()=>{
 let signal:AbortSignal|undefined;const fetch=vi.fn((url:RequestInfo|URL,init?:RequestInit)=>String(url).endsWith('/me')?Promise.resolve(new Response(JSON.stringify({data:{roles:['cs_agent']}}),{headers:{'X-CSRF-Token':'synthetic'}})):new Promise<Response>((_resolve,reject)=>{signal=init?.signal as AbortSignal;signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));}));
 vi.stubGlobal('fetch',fetch);setApiMode('live');const saved=vi.fn();const page=renderAt('/','/',<ImprovementEditor record={base().data.request} onSaved={saved} onReload={()=>{}}/>);edit();fireEvent.click(screen.getByText('내용 저장'));await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));fireEvent.click(screen.getByText('저장 중'));expect(fetch).toHaveBeenCalledTimes(2);page.unmount();expect(signal?.aborted).toBe(true);expect(saved).not.toHaveBeenCalled();
});
test('모의 응답과 완료된 요청은 편집을 열지 않는다',()=>{
 vi.stubGlobal('fetch',vi.fn());const data=base();renderAt('/','/',<ImprovementEditor record={data.data.request} onSaved={()=>{}} onReload={()=>{}}/>);expect((screen.getByText('내용 수정') as HTMLButtonElement).disabled).toBe(true);cleanup();setApiMode('live');data.data.request.status='COMPLETED';renderAt('/','/',<ImprovementEditor record={data.data.request} onSaved={()=>{}} onReload={()=>{}}/>);expect((screen.getByText('내용 수정') as HTMLButtonElement).disabled).toBe(true);expect(fetch).not.toHaveBeenCalled();
});
test('응답의 새 판본으로 두 번째 수정을 시작한다',async()=>{
 const sent:number[]=[];vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{if(String(url).endsWith('/me'))return new Response(JSON.stringify({data:{roles:['cs_agent']}}),{headers:{'X-CSRF-Token':'synthetic'}});const patch=JSON.parse(init!.body as string);sent.push(patch.version);const data=base();data.data.request.version=patch.version+1;data.data.request.problem_statement=patch.problem_statement;return jsonResponse(200,data);}));
 setApiMode('live');function Host(){const [data,setData]=useState(base);return <ImprovementEditor key={data.data.request.version} record={data.data.request} onSaved={setData} onReload={()=>{}}/>;}renderAt('/','/',<Host/>);edit();fireEvent.click(screen.getByText('내용 저장'));await screen.findByText('내용 수정');fireEvent.click(screen.getByText('내용 수정'));fireEvent.change(screen.getByLabelText('검토할 문제'),{target:{value:'두 번째 수정'}});fireEvent.click(screen.getByText('내용 저장'));await waitFor(()=>expect(sent).toEqual([1,2]));
});

test.each([401,503])('세션 조회 %i 실패는 쓰기를 실행하지 않는다',async(status)=>{
 const fetch=vi.fn(async()=>jsonResponse(status,errorBody('SESSION','세션 실패')));vi.stubGlobal('fetch',fetch);setApiMode('live');renderAt('/','/',<ImprovementEditor record={base().data.request} onSaved={()=>{}} onReload={()=>{}}/>);edit();fireEvent.click(screen.getByText('내용 저장'));await screen.findByRole('alert');expect(fetch).toHaveBeenCalledTimes(1);
});
test('CSRF가 없는 세션은 쓰기를 실행하지 않는다',async()=>{
 const fetch=vi.fn(async()=>jsonResponse(200,{data:{roles:['cs_agent']}}));vi.stubGlobal('fetch',fetch);setApiMode('live');renderAt('/','/',<ImprovementEditor record={base().data.request} onSaved={()=>{}} onReload={()=>{}}/>);edit();fireEvent.click(screen.getByText('내용 저장'));await screen.findByRole('alert');expect(fetch).toHaveBeenCalledTimes(1);
});
