// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import ProjectConstraintsForm from './ProjectConstraintsForm';
import { ProjectForm } from './IntakeForms';

const project = { id: '33333333-3333-4333-8333-333333333333', version: 2, supply_region: null, customer_manufacturer_list: null, off_list_permission: 'UNSTATED' as const };
const ok = (data: unknown) => jsonResponse(200, { data });
const posted: Record<string, unknown>[] = [];
let response: (body: Record<string, unknown>) => Promise<Response>;
beforeEach(() => {
  setApiMode('live'); posted.length = 0;
  response = async () => ok(project);
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if (!init?.method || init.method === 'GET') return ok({ ...project, version: 3, supply_region: '한국' });
    const body = JSON.parse(String(init.body)); posted.push(body);
    // 서버 enum·목록 계약을 검증한다. 임의 라벨도 성공시키는 stub이 아니다.
    if (!['ALLOWED', 'FORBIDDEN', 'UNSTATED'].includes(body.off_list_permission)
      || (body.customer_manufacturer_list !== null && (!body.customer_manufacturer_list.length || body.customer_manufacturer_list.some((s: string) => !s.trim())))) {
      return jsonResponse(422, errorBody('VALIDATION_ERROR', 'invalid constraints'));
    }
    return response(body);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

it('접수도 표시라벨 대신 코드값과 명시한 사람 입력을 보낸다', async () => {
  renderAt('/', '/', <ProjectForm onCancel={() => {}} />);
  fireEvent.change(screen.getByLabelText('프로젝트명'), { target: { value: '검토' } });
  fireEvent.change(screen.getByLabelText('고객 코드'), { target: { value: 'HOST' } });
  fireEvent.change(screen.getByLabelText('공급 지역'), { target: { value: ' 한국 ' } });
  fireEvent.change(screen.getByLabelText('고객 제조사 목록'), { target: { value: ' 제조사 A\n제조사 B ' } });
  fireEvent.change(screen.getByLabelText('목록 밖 제조사 허용'), { target: { value: 'FORBIDDEN' } });
  fireEvent.submit(screen.getByLabelText('프로젝트명').closest('form')!);
  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toMatchObject({ supply_region: '한국', customer_manufacturer_list: ['제조사 A', '제조사 B'], off_list_permission: 'FORBIDDEN' });
});

it('빈 목록은 null, 409 뒤 명시 최신조회와 재확인 전 자동 재전송하지 않는다', async () => {
  response = async () => jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', 'conflict'));
  renderAt('/', '/', <ProjectConstraintsForm project={project} onClose={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: '입력 저장' }));
  await screen.findByText(/프로젝트 판본이 바뀌었습니다/);
  expect(posted).toHaveLength(1);
  expect(posted[0]).toEqual({ version: 2, supply_region: null, customer_manufacturer_list: null, off_list_permission: 'UNSTATED' });
  expect((screen.getByRole('button', { name: '입력 저장' }) as HTMLButtonElement).disabled).toBe(true);
  response = async () => ok({ ...project, version: 4 });
  fireEvent.click(screen.getByRole('button', { name: '최신 입력 불러오기' }));
  await waitFor(() => expect((screen.getByLabelText('공급 지역') as HTMLInputElement).value).toBe('한국'));
  expect(posted).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '입력 저장' }));
  await waitFor(() => expect(posted).toHaveLength(2));
  expect(posted[1].version).toBe(3);
});

it('계정/프로젝트 전환으로 언마운트된 폼은 늦은 저장결과를 적용하지 않는다', async () => {
  let finish!: (value: Response) => void;
  response = () => new Promise(resolve => { finish = resolve; });
  const onClose = vi.fn();
  const view = renderAt('/', '/', <ProjectConstraintsForm project={project} onClose={onClose} />);
  fireEvent.click(screen.getByRole('button', { name: '입력 저장' }));
  await waitFor(() => expect(posted).toHaveLength(1));
  view.unmount();
  await act(async () => { finish(ok(project)); });
  expect(onClose).not.toHaveBeenCalled();
});
