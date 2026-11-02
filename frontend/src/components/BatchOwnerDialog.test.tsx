// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import BatchOwnerDialog from './BatchOwnerDialog';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });
const A = '11111111-1111-4111-8111-11111111111a';
const B = '11111111-1111-4111-8111-11111111111b';
const C = '11111111-1111-4111-8111-11111111111c';
const NEW = 'u2';
const flow = (owner: string | null, version: number) => ({ data: { ticket_id: 'x', state: 'IN_REVIEW', version, owner_user_id: owner, closed_at: null, closed_by: null, customer_sent_at: null, dept_results: [], transfers: [] } });
type Put = { url: string; body: Record<string, unknown> };

// 문의별 업무 흐름(담당·판본)과 PUT 응답을 정해 둔 가짜 서버.
function server(flows: Record<string, ReturnType<typeof flow>>, put: (id: string) => Response = () => jsonResponse(200, flow(NEW, 9))) {
  const puts: Put[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'm1', roles: ['manager'] } }), { status: 200, headers: { 'X-CSRF-Token': 'tok' } });
    if (path.includes('/platform/assignees')) return jsonResponse(200, { data: { items: [{ user_id: 'u1', display_name: '김지원' }, { user_id: NEW, display_name: '박민수' }] } });
    const id = Object.keys(flows).find(key => path.includes(key))!;
    if (init?.method === 'PUT') { puts.push({ url: path, body: JSON.parse(String(init.body)) }); return put(id); }
    return jsonResponse(200, flows[id]);
  }));
  return puts;
}
function view(targets = [A, B, C]) {
  setApiMode('live');
  const onDone = vi.fn();
  renderAt('/x', '/x', <BatchOwnerDialog targets={targets.map((id, i) => ({ id, label: `CS-${i + 1}` }))} onClose={vi.fn()} onDone={onDone} />);
  return onDone;
}
const choose = async () => { fireEvent.change(await screen.findByLabelText('새 담당'), { target: { value: NEW } }); fireEvent.click(screen.getByRole('button', { name: '변경' })); };
const results = () => within(screen.getByRole('list', { name: '담당 변경 결과' })).getAllByRole('listitem').map(item => item.textContent);

test('문의마다 최신 판본을 읽어 담당을 바꾸고, 이미 그 담당인 문의는 보내지 않는다', async () => {
  const puts = server({ [A]: flow('u1', 3), [B]: flow(NEW, 5), [C]: flow(null, 7) });
  const onDone = view();
  expect((screen.getByRole('button', { name: '변경' }) as HTMLButtonElement).disabled).toBe(true);
  await choose();
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain('끝남: 바꿈 2, 건너뜀 1, 실패 0, 결과 모름 0'));
  expect(puts.map(put => [put.url.split('/tickets/')[1], put.body])).toEqual([[`${A}/workflow/owner`, { owner_user_id: NEW, version: 3 }], [`${C}/workflow/owner`, { owner_user_id: NEW, version: 7 }]]);
  expect(results()).toEqual(['CS-1 · 바꿈', 'CS-2 · 건너뜀 · 이미 그 담당입니다', 'CS-3 · 바꿈']);
  expect(onDone).toHaveBeenCalled();
});

test('한 건이 실패해도 나머지는 계속하고, 판본 충돌·담당 불가·결과 모름을 건별로 알린다', async () => {
  const replies: Record<string, () => Response> = {
    [A]: () => jsonResponse(409, errorBody('CS_WORKFLOW_VERSION_CONFLICT', '판본 다름')),
    [B]: () => jsonResponse(503, errorBody('UNAVAILABLE', '일시 장애')),
    [C]: () => jsonResponse(422, errorBody('CS_INVALID_OWNER', '담당 불가')),
  };
  const puts = server({ [A]: flow('u1', 1), [B]: flow('u1', 2), [C]: flow('u1', 3) }, id => replies[id]());
  view();
  await choose();
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain('끝남: 바꿈 0, 건너뜀 0, 실패 2, 결과 모름 1'));
  expect(puts).toHaveLength(3);
  const lines = results();
  expect(lines[0]).toContain('그 사이 상태가 바뀌었습니다');
  expect(lines[1]).toContain('결과 모름 · 다시 보내지 않았습니다');
  expect(lines[2]).toContain('활성 C/S 담당자가 아닙니다');
  // 끝나면 변경 단추를 없애 같은 요청을 다시 보내지 않는다.
  expect(screen.queryByRole('button', { name: '변경' })).toBeNull();
});
