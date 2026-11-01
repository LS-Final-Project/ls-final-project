import { focusManager } from '@tanstack/react-query';
import { afterEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import CsImprovements from './CsImprovements';
import type { ImprovementDetail } from '../api/cs-improvements';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';
const source=examples.find(x=>x.path==='/api/v1/cs/improvement-requests/dddddddd-dddd-4ddd-8ddd-ddddddddddd1')!.body;
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.unstubAllEnvs();setApiMode('mock');});
// 상세 화면은 역할 확인용 세션 조회(/me)를 함께 부른다. 상세 조회 횟수만 센다.
const detailCalls=(fetch:{mock:{calls:unknown[][]}})=>fetch.mock.calls.filter(([url])=>!String(url).endsWith('/me'));
function view(detail=true){setApiMode('live');return renderAt(detail?'/cs/improvements/test':'/cs/improvements',detail?'/cs/improvements/:requestId':'/cs/improvements',<CsImprovements/>);}
test('근거·조건·회신·조치를 분리하고 확인되지 않은 고객 적용을 추측하지 않는다',async()=>{
 const fetch=vi.fn(async()=>jsonResponse(200,source));vi.stubGlobal('fetch',fetch);view();
 await screen.findByText('근거 문의');expect(screen.getByText('부서 조치')).toBeTruthy();expect(screen.getByText(/고객별 적용·재발 여부는 이 응답만으로/)).toBeTruthy();expect(screen.queryByRole('button',{name:'전달'})).toBeNull();expect(detailCalls(fetch)).toHaveLength(1);
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
 let count=0;const fetch=vi.fn((url:RequestInfo|URL,init?:RequestInit)=>{if(String(url).endsWith('/me'))return Promise.resolve(jsonResponse(401,errorBody('SESSION','세션 없음')));if(count++===0)return Promise.resolve(jsonResponse(200,source));return new Promise<Response>((_resolve,reject)=>init?.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));});
 vi.stubGlobal('fetch',fetch);const page=view();await screen.findByText('근거 문의');fireEvent.click(screen.getByText('최신 요청 조회'));await waitFor(()=>expect(detailCalls(fetch)).toHaveLength(2));expect(screen.queryByText('근거 문의')).toBeNull();page.unmount();
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

// Window focus and manual refresh must not discard an open form or abort its save.
test.each([false,true])('편집 중 포커스·재조회가 입력과 저장 결과를 보존한다 (detail=%s)',async(detail)=>{
 let release!:()=>void;let saveSignal:AbortSignal|undefined;
 const pending=new Promise<void>(resolve=>{release=resolve;});
 const fetch=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
  if(String(url).endsWith('/me'))return new Response(JSON.stringify({data:{roles:['cs_agent']}}),{headers:{'X-CSRF-Token':'synthetic'}});
  if(init?.method==='POST'||init?.method==='PATCH'){saveSignal=init.signal as AbortSignal;await pending;return jsonResponse(503,errorBody('UNCERTAIN','합성 저장 결과 불확실'));}
  return jsonResponse(200,detail?source:{data:{items:[]}});
 });vi.stubGlobal('fetch',fetch);view(detail);
 fireEvent.click(await screen.findByRole('button',{name:detail?'내용 수정':'새 개선요청 작성'}));
 fireEvent.change(screen.getByLabelText('검토할 문제'),{target:{value:'보존할 합성 입력'}});
 if(!detail){fireEvent.change(screen.getByLabelText('요청 유형'),{target:{value:'DOCUMENT'}});fireEvent.change(screen.getByLabelText('제품 코드'),{target:{value:'SYNTHETIC'}});fireEvent.change(screen.getByLabelText('문서 신호'),{target:{value:'NOT_FOUND'}});}
 const refresh=screen.getByRole('button',{name:'최신 요청 조회'});
 expect((refresh as HTMLButtonElement).disabled).toBe(true);
 await act(async()=>{focusManager.setFocused(false);focusManager.setFocused(true);});
 expect((screen.getByLabelText('검토할 문제') as HTMLTextAreaElement).value).toBe('보존할 합성 입력');
 fireEvent.click(screen.getByRole('button',{name:detail?'내용 저장':'초안 저장'}));
 await waitFor(()=>expect(saveSignal).toBeTruthy());
 fireEvent.click(refresh);
 await act(async()=>{focusManager.setFocused(false);focusManager.setFocused(true);});
 expect(saveSignal?.aborted).toBe(false);
 await act(async()=>{release();});
 await screen.findByText('합성 저장 결과 불확실');
 expect((screen.getByRole('button',{name:detail?'내용 저장':'초안 저장'}) as HTMLButtonElement).disabled).toBe(true);
 expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST'||init?.method==='PATCH')).toHaveLength(1);
 if(!detail){fireEvent.click(screen.getByRole('button',{name:'입력 버리고 목록 새로 조회'}));await screen.findByRole('button',{name:'새 개선요청 작성'});expect(screen.queryByText('합성 저장 결과 불확실')).toBeNull();expect(fetch.mock.calls.filter(([url,init])=>String(url).endsWith('/improvement-requests')&&!init?.method)).toHaveLength(2);expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1);}
 focusManager.setFocused(undefined);
});

// 운영 빌드(DEV 꺼짐)에서도 업무 UI가 켜져 있고 C/S 역할이면 상세와 처리 동작을 연다. 업무 UI 설정은 모듈을 읽을 때 정해져 새로 불러온다.
test('운영 빌드는 준비 중 대신 실제 상세와 역할별 동작을 보인다',async()=>{
 vi.stubEnv('DEV',false);vi.stubEnv('VITE_BUSINESS_UI_ENABLED','true');vi.resetModules();
 const [{default:Page},{renderAt:render}]=await Promise.all([import('./CsImprovements'),import('../test/render')]);
 const fetch=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:['cs_agent'],dept:null,managed_departments:[]}}),{headers:{'X-CSRF-Token':'synthetic'}}):jsonResponse(200,source));
 vi.stubGlobal('fetch',fetch);
 render('/cs/improvements/test','/cs/improvements/:requestId',<Page/>);
 await screen.findByText('근거 문의');expect(screen.queryByText(/운영 인증 중계와 권한 검증 후/)).toBeNull();
 expect(await screen.findByRole('button',{name:'검토 완료 기록'})).toBeTruthy();expect(screen.getByRole('button',{name:'내용 수정'})).toBeTruthy();
});
test('운영 빌드에서 업무 UI가 꺼져 있으면 조회하지 않는다',async()=>{
 vi.stubEnv('DEV',false);vi.stubEnv('VITE_BUSINESS_UI_ENABLED','false');vi.resetModules();
 const [{default:Page},{renderAt:render}]=await Promise.all([import('./CsImprovements'),import('../test/render')]);
 const fetch=vi.fn(async()=>jsonResponse(200,source));vi.stubGlobal('fetch',fetch);
 render('/cs/improvements/test','/cs/improvements/:requestId',<Page/>);
 await screen.findByRole('heading',{name:'개선요청'});expect(fetch).not.toHaveBeenCalled();expect(screen.queryByText('근거 문의')).toBeNull();
});
