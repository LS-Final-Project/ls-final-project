import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import CsStats from './CsStats';
import { statsQuery } from '../api/cs-stats';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';
const example=examples.find(x=>x.path==='/api/v1/cs/stats/overview')!.body;
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');});
function view(){setApiMode('live');return renderAt('/cs/stats','/cs/stats',<CsStats/>);}
test('저장된 집계와 미확인·출처를 구분하고 기간을 timezone 포함으로 보낸다',async()=>{
 const fetch=vi.fn(async(_url:RequestInfo|URL)=>jsonResponse(200,example));vi.stubGlobal('fetch',fetch);view();
 await screen.findByText(/처리 현황 \(/);expect(screen.getByText(/SLA 지연 아님/)).toBeTruthy();expect(screen.getByText(/미확인 지표:/)).toBeTruthy();
 fireEvent.change(screen.getByLabelText('시작 (포함, 현지 시각)'),{target:{value:'2026-10-01T00:00'}});
 fireEvent.change(screen.getByLabelText('종료 (미포함, 현지 시각)'),{target:{value:'2026-09-01T00:00'}});
 fireEvent.click(screen.getByText('필터 적용'));await screen.findByText('종료는 시작보다 늦어야 합니다.');expect(fetch).toHaveBeenCalledTimes(1);
 fireEvent.change(screen.getByLabelText('종료 (미포함, 현지 시각)'),{target:{value:'2026-11-01T00:00'}});fireEvent.click(screen.getByText('필터 적용'));
 await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));expect(String(fetch.mock.calls[1]?.[0])).toContain(encodeURIComponent(new Date('2026-10-01T00:00').toISOString()));
});
test.each([401,403,503])('갱신 %i는 이전 집계를 숨긴다',async(status)=>{
 let fail=false;vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(fail?status:200,fail?errorBody('FAIL','합성 실패'):example)));view();
 await screen.findByText(/처리 현황 \(/);fail=true;fireEvent.click(screen.getByText('최신 현황 조회'));await screen.findByRole('alert');expect(screen.queryByText(/처리 현황 \(/)).toBeNull();
 fail=false;fireEvent.click(screen.getByText('최신 현황 조회'));await screen.findByText(/처리 현황 \(/);
});
test('고객 이력은 고객 ID를 요구하고 부서 필터를 제외하며 가정 장비를 표시한다',async()=>{
 const fetch=vi.fn(async(url:RequestInfo|URL)=>jsonResponse(200,String(url).includes('/customers/')?{data:{customer_external_id:'TEST/ONE',received:0,unconfirmed:[],by_state:{},by_product:{},tickets:[],units:[{id:'unit',serial_no:'SYNTHETIC-UNIT',product_code:'TEST',unit_status:'ACTIVE',derivation_kind:'ASSUMED',confirmed_tickets:0}],improvement_requests:[]}}:example));vi.stubGlobal('fetch',fetch);view();await screen.findByText(/처리 현황 \(/);
 fireEvent.click(screen.getByText('고객·장비 이력'));fireEvent.click(screen.getByText('필터 적용'));await screen.findByText('고객 ID를 입력하세요.');
 fireEvent.change(screen.getByLabelText('고객 ID'),{target:{value:'TEST/ONE'}});fireEvent.click(screen.getByText('필터 적용'));await screen.findByText(/SYNTHETIC-UNIT.*가정 자료/);expect(String(fetch.mock.calls.at(-1)?.[0])).toBe('/api/v1/cs/stats/customers/TEST%2FONE');
});
test('담당자 미배정과 기간 밖 진행 부담을 구분하고 고객 필터를 보내지 않는다',async()=>{
 const fetch=vi.fn(async(url:RequestInfo|URL)=>jsonResponse(200,String(url).includes('/owners')?{data:{period:{start:null,end:null},items:[{owner_user_id:null,received:0,closed:0,open:3}]}}:example));vi.stubGlobal('fetch',fetch);view();await screen.findByText(/처리 현황 \(/);
 fireEvent.click(screen.getByText('담당자별 현황 (관리자)'));await screen.findByText('담당자 미배정');expect(screen.getByText('현재 진행 (전체 기간)')).toBeTruthy();expect(String(fetch.mock.calls.at(-1)?.[0])).toBe('/api/v1/cs/stats/owners');
});
test('반복 클릭은 대기 요청을 늘리지 않고 화면 이탈은 취소, 재진입은 재조회한다',async()=>{
 let signal:AbortSignal|undefined;let calls=0;vi.stubGlobal('fetch',vi.fn((_url:RequestInfo|URL,init?:RequestInit)=>{calls++;if(calls>1)return Promise.resolve(jsonResponse(200,example));signal=init?.signal as AbortSignal;return new Promise<Response>((_r,reject)=>signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));}));
 const page=view();await waitFor(()=>expect(calls).toBe(1));fireEvent.click(screen.getByText('최신 현황 조회'));expect(calls).toBe(1);fireEvent.click(screen.getByText('처리·개선 현황'));expect(signal?.aborted).toBe(false);page.unmount();expect(signal?.aborted).toBe(true);view();await screen.findByText(/처리 현황 \(/);
});

test('URLSearchParams.size가 없는 환경에서도 필터를 보존한다',()=>{
 const descriptor=Object.getOwnPropertyDescriptor(URLSearchParams.prototype,'size');
 try {Object.defineProperty(URLSearchParams.prototype,'size',{configurable:true,get:()=>undefined});expect(statsQuery({start:'',end:'',dept:'TS_LV',customer:'TEST/ONE'},'overview')).toBe('?dept=TS_LV&customer=TEST%2FONE');expect(statsQuery({start:'',end:'',dept:'',customer:''},'overview')).toBe('');}
 finally {if(descriptor)Object.defineProperty(URLSearchParams.prototype,'size',descriptor);else Reflect.deleteProperty(URLSearchParams.prototype,'size');}
});

test('재조회 응답을 기다리는 동안 이전 표시를 숨긴다',async()=>{
 let count=0;const fetch=vi.fn((_url:RequestInfo|URL,init?:RequestInit)=>{if(count++===0)return Promise.resolve(jsonResponse(200,example));return new Promise<Response>((_resolve,reject)=>init?.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));});
 vi.stubGlobal('fetch',fetch);const page=view();await screen.findByText(/처리 현황 \(/);fireEvent.click(screen.getByText('최신 현황 조회'));await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));expect(screen.queryByText(/처리 현황 \(/)).toBeNull();page.unmount();
});
