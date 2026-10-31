import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import ImprovementCreator from './ImprovementCreator';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import { type ImprovementDetail } from '../api/cs-improvements';
import examples from '../mocks/generated/cs.json';
import { errorBody, jsonResponse, renderAt } from '../test/render';
const result=examples.find(x=>x.path.includes('/improvement-requests/'))!.body as unknown as ImprovementDetail;
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');clearSessionCsrfToken();});
function setup(status=201,role='cs_agent',csrf=true){
  const created=vi.fn();
  const fetch=vi.fn(async(url:RequestInfo|URL,_init?:RequestInit)=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:[role]}}),{headers:csrf?{'X-CSRF-Token':'synthetic'}:{}}):jsonResponse(status,status===201?result:errorBody('FAIL','합성 작성 실패')));
  vi.stubGlobal('fetch',fetch);setApiMode('live');const page=renderAt('/','/',<ImprovementCreator onCreated={created}/>);return {fetch,created,page};
}
function form(type='FAULT'){
  fireEvent.click(screen.getByText('새 개선요청 작성'));
  fireEvent.change(screen.getByLabelText('요청 유형'),{target:{value:type}});
  fireEvent.change(screen.getByLabelText('제품 코드'),{target:{value:' 합성제품 '}});
  fireEvent.change(screen.getByLabelText('검토할 문제'),{target:{value:' 합성 검토 문제 '}});
  if(type==='FAULT'){fireEvent.change(screen.getByLabelText('고장 ID'),{target:{value:'FLT-001'}});fireEvent.click(screen.getByLabelText('TS_LV'));}
  else fireEvent.change(screen.getByLabelText('문서 신호'),{target:{value:'NOT_FOUND'}});
}
test('고장 초안은 사용자 입력·미확인 조건만 POST하고 실제 응답을 전달한다',async()=>{
  const {fetch,created}=setup();form();fireEvent.click(screen.getByText('초안 저장'));await waitFor(()=>expect(created).toHaveBeenCalledWith(result));
  const init=fetch.mock.calls[1][1]!;expect(init.method).toBe('POST');expect(init.headers).toMatchObject({'X-CSRF-Token':'synthetic'});
  expect(JSON.parse(init.body as string)).toEqual({request_type:'FAULT',product_code:'합성제품',fault_id:'FLT-001',proposed_depts:['TS_LV'],problem_statement:'합성 검토 문제',condition_status:'UNKNOWN',evidence:[]});
});
test('유형 전환은 이전 고장/부서를 버리고 수신 미확인 문서 초안을 작성한다',async()=>{
  const {fetch,created}=setup();form();fireEvent.change(screen.getByLabelText('요청 유형'),{target:{value:'DOCUMENT'}});fireEvent.change(screen.getByLabelText('문서 신호'),{target:{value:'BROKEN_LINK'}});
  expect(screen.getByText(/수신 부서 미확인 초안/)).toBeTruthy();fireEvent.click(screen.getByText('초안 저장'));await waitFor(()=>expect(created).toHaveBeenCalled());
  const body=JSON.parse(fetch.mock.calls[1][1]!.body as string);expect(body).toMatchObject({request_type:'DOCUMENT',doc_signal:'BROKEN_LINK',proposed_depts:[]});expect(body).not.toHaveProperty('fault_id');
});
test('필수값·고장 형식·제안 부서를 검사하고 취소/재진입은 초기화와 초점을 보장한다',()=>{
  const {fetch}=setup();form();fireEvent.change(screen.getByLabelText('고장 ID'),{target:{value:'bad'}});expect((screen.getByText('초안 저장') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('고장 ID'),{target:{value:'FLT-001'}});fireEvent.click(screen.getByLabelText('TS_LV'));expect((screen.getByText('초안 저장') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByText('작성 취소'));expect(document.activeElement).toBe(screen.getByText('새 개선요청 작성'));fireEvent.click(screen.getByText('새 개선요청 작성'));
  expect(document.activeElement).toBe(screen.getByLabelText('요청 유형'));expect((screen.getByLabelText('제품 코드') as HTMLInputElement).value).toBe('');expect(fetch).not.toHaveBeenCalled();
});
test.each(['manager','dept_staff'])('권한 %s는 POST 전 거부한다',async role=>{const {fetch,created}=setup(201,role);form();fireEvent.click(screen.getByText('초안 저장'));await screen.findByRole('alert');expect(fetch).toHaveBeenCalledTimes(1);expect(created).not.toHaveBeenCalled();});
test('CSRF 없는 세션은 POST하지 않는다',async()=>{const {fetch}=setup(201,'cs_agent',false);form();fireEvent.click(screen.getByText('초안 저장'));await screen.findByRole('alert');expect(fetch).toHaveBeenCalledTimes(1);});
test.each([401,403])('세션 %i 실패 시 생성하지 않는다',async status=>{const {fetch}=setup();fetch.mockImplementation(async()=>jsonResponse(status,errorBody('SESSION','세션 확인 실패')));form();fireEvent.click(screen.getByText('초안 저장'));await screen.findByRole('alert');expect(fetch).toHaveBeenCalledTimes(1);});
test.each([408,409,503])('작성 %i는 반복 생성과 취소 후 재작성 대신 목록 확인을 요구한다',async status=>{
  const {fetch,created}=setup(status);form();fireEvent.click(screen.getByText('초안 저장'));await screen.findByRole('alert');expect(screen.getByText(/저장 결과가 불확실해/)).toBeTruthy();expect((screen.getByText('초안 저장') as HTMLButtonElement).disabled).toBe(true);expect((screen.getByText('작성 취소') as HTMLButtonElement).disabled).toBe(true);fireEvent.click(screen.getByText('초안 저장'));expect(fetch).toHaveBeenCalledTimes(2);expect(created).not.toHaveBeenCalled();
});
test('422는 입력 보완 후 재시도 가능하다',async()=>{
  const {fetch,created}=setup(422);form();fireEvent.click(screen.getByText('초안 저장'));await screen.findByRole('alert');fetch.mockImplementation(async url=>String(url).endsWith('/me')?new Response(JSON.stringify({data:{roles:['cs_agent']}}),{headers:{'X-CSRF-Token':'synthetic'}}):jsonResponse(201,result));fireEvent.change(screen.getByLabelText('제품 코드'),{target:{value:'보완제품'}});fireEvent.click(screen.getByText('초안 저장'));await waitFor(()=>expect(created).toHaveBeenCalledTimes(1));
});
test('네트워크 결과 불명은 자동 재시도하지 않는다',async()=>{
  const {fetch}=setup();fetch.mockImplementation(async url=>{if(String(url).endsWith('/me'))return new Response(JSON.stringify({data:{roles:['cs_agent']}}),{headers:{'X-CSRF-Token':'synthetic'}});throw new TypeError('network');});form();fireEvent.click(screen.getByText('초안 저장'));await screen.findByRole('alert');expect((screen.getByText('초안 저장') as HTMLButtonElement).disabled).toBe(true);expect(fetch).toHaveBeenCalledTimes(2);
});
test('지연 저장 중 반복 클릭은 한 번만 생성하고 이탈 시 요청을 취소한다',async()=>{
  const {fetch,page,created}=setup();let signal:AbortSignal|undefined;
  fetch.mockImplementation((url,init)=>String(url).endsWith('/me')?Promise.resolve(new Response(JSON.stringify({data:{roles:['cs_agent']}}),{headers:{'X-CSRF-Token':'synthetic'}})):new Promise<Response>((_resolve,reject)=>{signal=init?.signal as AbortSignal;signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));}));
  form();fireEvent.click(screen.getByText('초안 저장'));await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));fireEvent.click(screen.getByText('초안 저장 중'));expect(fetch).toHaveBeenCalledTimes(2);page.unmount();expect(signal?.aborted).toBe(true);expect(created).not.toHaveBeenCalled();
});
test('모의 응답에서는 작성 버튼이 비활성이고 요청을 보내지 않는다',()=>{const {fetch}=setup();cleanup();setApiMode('mock');renderAt('/','/',<ImprovementCreator onCreated={()=>{}}/>);expect((screen.getByText('새 개선요청 작성') as HTMLButtonElement).disabled).toBe(true);expect(fetch).not.toHaveBeenCalled();});
