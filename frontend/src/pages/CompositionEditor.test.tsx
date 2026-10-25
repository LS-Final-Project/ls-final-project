import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import CompositionEditor from './CompositionEditor';
import examples from '../mocks/generated/tft.json';
import type { ProjectDetail } from '../api/tft';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
const source=examples.find(x=>x.path==='/api/v1/tft/projects/33333333-3333-4333-8333-333333333333')!.body.data as unknown as ProjectDetail;
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');});
function view(project=structuredClone(source),userId=source.responsible_user_id!){setApiMode('live');const refresh=vi.fn(async()=>{});renderAt('/','/',<CompositionEditor project={project} assessment={null} userId={userId} disabled={false} onRefresh={refresh}/>);return refresh;}
function note(){fireEvent.change(screen.getByLabelText('구성안 변경 사유'),{target:{value:'합성 검증 구성 변경'}});}
test('모든 역할에 후보 또는 공백을 기록해야 한다',()=>{view({...source,current_composition:null});note();fireEvent.click(screen.getByRole('button',{name:'새 판본 저장'}));expect(screen.getByText('모든 역할에 후보를 선택하거나 공백 사유를 기록하세요.')).toBeTruthy();});
test('공백은 사유가 있어야 저장 확인으로 넘어간다',()=>{view();note();fireEvent.click(screen.getByRole('button',{name:'케이블 시스템 설계 공백으로 기록'}));fireEvent.click(screen.getByRole('button',{name:'새 판본 저장'}));expect(screen.getByText('공백 사유와 보완 조건을 확인하세요.')).toBeTruthy();});
test('현재 책임자가 아니거나 검증이 미완료면 역할 확정을 선택할 수 없다',()=>{view(source,'other');expect(screen.getAllByRole('option',{name:'역할 확정'}).every(x=>(x as HTMLOptionElement).disabled)).toBe(true);});
test('확인 취소는 생성하지 않는다',()=>{const f=vi.fn();vi.stubGlobal('fetch',f);view();note();fireEvent.click(screen.getByRole('button',{name:'새 판본 저장'}));fireEvent.click(screen.getByRole('button',{name:'구성안 저장 취소'}));expect(f).not.toHaveBeenCalled();expect((screen.getByLabelText('구성안 변경 사유') as HTMLTextAreaElement).value).toBe('합성 검증 구성 변경');});
test('새 판본은 전체 역할과 현재 판본을 보내고 성공 후 조회한다',async()=>{const f=vi.fn(async()=>jsonResponse(200,{data:{composition_version:2,project_version:2}}));vi.stubGlobal('fetch',f);const refresh=view();note();fireEvent.click(screen.getByRole('button',{name:'새 판본 저장'}));fireEvent.click(screen.getByRole('button',{name:'새 판본 저장 확인'}));await screen.findByText('새 구성안 판본을 저장했습니다. 이전 판본은 보존됩니다.');const args=f.mock.calls as unknown as [string,RequestInit][];const body=JSON.parse(args[0][1].body as string);expect(body.members).toHaveLength(source.required_roles.length);expect((args[0][1].headers as Record<string,string>)['If-Match']).toBe('"1"');expect(refresh).toHaveBeenCalledOnce();});
test('충돌은 입력을 보존하고 새 저장 확인을 요구한다',async()=>{vi.stubGlobal('fetch',vi.fn(async()=>jsonResponse(412,errorBody('VERSION_CONFLICT','충돌'))));view();note();fireEvent.click(screen.getByRole('button',{name:'새 판본 저장'}));fireEvent.click(screen.getByRole('button',{name:'새 판본 저장 확인'}));await screen.findByText(/입력은 보존했습니다/);expect((screen.getByLabelText('구성안 변경 사유') as HTMLTextAreaElement).value).toBe('합성 검증 구성 변경');expect(screen.queryByRole('button',{name:'새 판본 저장 확인'})).toBeNull();});
