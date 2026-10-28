import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import CsImprovements from './CsImprovements';
import type { ImprovementDetail } from '../api/cs-improvements';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';
const source=examples.find(x=>x.path==='/api/v1/cs/improvement-requests/dddddddd-dddd-4ddd-8ddd-ddddddddddd1')!.body;
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');});
function view(detail=true){setApiMode('live');return renderAt(detail?'/cs/improvements/test':'/cs/improvements',detail?'/cs/improvements/:requestId':'/cs/improvements',<CsImprovements/>);}
test('근거·조건·회신·조치를 분리하고 확인되지 않은 고객 적용을 추측하지 않는다',async()=>{
 const fetch=vi.fn(async()=>jsonResponse(200,source));vi.stubGlobal('fetch',fetch);view();
 await screen.findByText('근거 문의');expect(screen.getByText('부서 조치')).toBeTruthy();expect(screen.getByText(/고객별 적용·재발 여부는 이 응답만으로/)).toBeTruthy();expect(screen.queryByRole('button',{name:'전달'})).toBeNull();expect(fetch).toHaveBeenCalledTimes(1);
});
test('빈 목록은 완료 건수가 아닌 조회 가능한 기록 없음으로 표시한다',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,{data:{items:[]}})));view(false);await screen.findByText('조회 가능한 개선요청이 없습니다.');
});
test.each([401,403,404,503])('오류 %i 뒤 이전 상세를 숨기고 복구한다',async(status)=>{
 let fail=false;vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(fail?status:200,fail?errorBody('FAIL','합성 오류'):source)));view();await screen.findByText('근거 문의');fail=true;fireEvent.click(screen.getByText('최신 요청 조회'));await screen.findByRole('alert');expect(screen.queryByText('근거 문의')).toBeNull();fail=false;fireEvent.click(screen.getByText('최신 요청 조회'));await screen.findByText('근거 문의');
});
test('미배정·조건 미확인·제외 근거를 성공으로 표시하지 않는다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;body.data.request.condition_status='UNKNOWN';body.data.request.receiver_unconfirmed=true;body.data.next_owners=[{actor:'CS_OWNER',user_id:null,dept_code:null,task:'REVIEW'}];body.data.evidence=[{id:'e',ticket_id:'t',ticket_external_id:'SYNTHETIC',case_id:null,case_external_id:null,added_by:null,added_at:'2026-10-27',removed_by:null,removed_at:'2026-10-27',removal_reason:'제품 불일치'}];
 vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,body)));view();await screen.findByText(/수신 부서 미확인/);expect(screen.getByText(/담당자 미배정/)).toBeTruthy();expect(screen.getByText(/제외됨: 제품 불일치/)).toBeTruthy();
});
test('대기중 반복 클릭을 막고 이탈 시 조회를 취소한다',async()=>{
 let signal:AbortSignal|undefined;const fetch=vi.fn((_url:RequestInfo|URL,init?:RequestInit)=>{signal=init?.signal as AbortSignal;return new Promise<Response>((_r,reject)=>signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));});vi.stubGlobal('fetch',fetch);const page=view();await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));fireEvent.click(screen.getByText('최신 요청 조회'));expect(fetch).toHaveBeenCalledTimes(1);page.unmount();expect(signal?.aborted).toBe(true);
});

test('목록은 실제 응답 제목과 상세 링크를 표시한다',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(200,{data:{items:[{id:'request-one',problem_statement:'합성 문서 검토',request_type:'DOCUMENT',product_code:'TEST',status:'DRAFT',version:1,evidence_count:0,cs_owner:null}]}})));view(false);const link=await screen.findByRole('link',{name:'합성 문서 검토'});expect(link.getAttribute('href')).toBe('/cs/improvements/request-one');expect(screen.getByText('담당자 ID: 미배정')).toBeTruthy();
});

test('재조회 응답을 기다리는 동안 이전 표시를 숨긴다',async()=>{
 let count=0;const fetch=vi.fn((_url:RequestInfo|URL,init?:RequestInit)=>{if(count++===0)return Promise.resolve(jsonResponse(200,source));return new Promise<Response>((_resolve,reject)=>init?.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));});
 vi.stubGlobal('fetch',fetch);const page=view();await screen.findByText('근거 문의');fireEvent.click(screen.getByText('최신 요청 조회'));await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));expect(screen.queryByText('근거 문의')).toBeNull();page.unmount();
});


test('초안 생성 후 실제 반환 ID의 상세를 새로 조회한다',async()=>{
 const body=structuredClone(source) as unknown as ImprovementDetail;body.data.request.id='created-synthetic';body.data.request.problem_statement='새 합성 초안';
 const fetch=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
  if(String(url).endsWith('/me'))return new Response(JSON.stringify({data:{roles:['cs_agent']}}),{headers:{'X-CSRF-Token':'synthetic'}});
  if(init?.method==='POST')return jsonResponse(201,body);
  return jsonResponse(200,String(url).endsWith('/created-synthetic')?body:{data:{items:[]}});
 });vi.stubGlobal('fetch',fetch);setApiMode('live');
 renderAt('/cs/improvements','/cs/improvements/*',<Routes><Route index element={<CsImprovements/>}/><Route path=":requestId" element={<CsImprovements/>}/></Routes>);
 fireEvent.click(await screen.findByText('새 개선요청 작성'));
 fireEvent.change(screen.getByLabelText('요청 유형'),{target:{value:'DOCUMENT'}});
 fireEvent.change(screen.getByLabelText('제품 코드'),{target:{value:'SYNTHETIC'}});
 fireEvent.change(screen.getByLabelText('문서 신호'),{target:{value:'NOT_FOUND'}});
 fireEvent.change(screen.getByLabelText('검토할 문제'),{target:{value:'새 합성 초안'}});
 fireEvent.click(screen.getByText('초안 저장'));await screen.findByRole('heading',{name:'개선요청 상세'});
 await screen.findByRole('heading',{name:'새 합성 초안'});expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1);
 expect(fetch.mock.calls.some(([url])=>String(url).endsWith('/created-synthetic'))).toBe(true);
});
