import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import tftExamples from '../mocks/generated/tft.json';
import { setApiMode } from '../api/client';
import type { StaffingRequest } from '../api/tft';
import { errorBody, jsonResponse, renderApp } from '../test/render';

const PROJECT_ID = '33333333-3333-4333-8333-333333333333';
const ROLE_ID = '88888888-8888-4888-8888-888888888802';
const PROJECT_PATH = `/api/v1/tft/projects/${PROJECT_ID}`;
const LIST_PATH = `${PROJECT_PATH}/staffing-requests`;
const project = tftExamples.find(item => item.path === PROJECT_PATH)!.body;

// 테스트 전용 요청서. 모의 응답 파일에는 넣지 않는다(KAN-122 계약 필드 그대로).
const draft: StaffingRequest = {
  id: '99999999-9999-4999-8999-999999999912', project_id: PROJECT_ID, required_role_id: ROLE_ID, role_name: '해상 시공 관리',
  organization_id: '66666666-6666-4666-8666-666666666602', organization_name: '합성 시공팀', organization_dept_code: 'CON',
  participation: 'LEAD', status: 'DRAFT', version: 1, due_at: null, due_undecided: false, message: null,
  issued_by_user_id: null, issued_at: null, created_at: '2026-10-23T00:00:00Z', updated_at: '2026-10-23T00:00:00Z', data_origin: 'synthetic', derivation_kind: 'SYSTEM',
};

// items는 목록 응답이 매번 읽는 배열이다. onPatch가 이 배열을 바꾸면 재조회가 새 판본을 돌려준다.
function stubLive(items: StaffingRequest[], onPatch: (init: RequestInit) => Response, onIssue: (init: RequestInit) => Response = () => jsonResponse(500, {}), identity: () => Response | Promise<Response> = () => jsonResponse(200, { data: { user_id: 'sales-test', roles: ['sales'] } })) {
  setApiMode('live');
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/v1/platform/me') return identity();
    if (url === PROJECT_PATH) return jsonResponse(200, project);
    if (url === LIST_PATH) return jsonResponse(200, { data: { items: [...items] }, meta: { request_id: '11111111-1111-4111-8111-111111111112' } });
    if (init?.method === 'PATCH') return onPatch(init);
    if (init?.method === 'POST' && url.endsWith('/issue')) return onIssue(init);
    return jsonResponse(401, errorBody('AUTH_REQUIRED', '로그인 필요'));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

describe('S2 역할·인력 요청서', () => {
  test('모의 응답: 역할 요건·필수/우대 역량·연결 요구사항 근거를 그리고 요청서 목록은 모의 응답 유무를 따른다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, errorBody('AUTH_REQUIRED', '로그인 필요'))));
    renderApp(`/tft/projects/${PROJECT_ID}/roles/${ROLE_ID}`);
    expect(await screen.findByRole('heading', { level: 1, name: '해상 시공 관리' })).toBeTruthy();
    expect(screen.getByText('해상 설치 조건·시공 위험 검토 역량')).toBeTruthy();
    expect(screen.getByText('유사 해저 시공 참여 경험')).toBeTruthy();
    expect(screen.getByText('R-01')).toBeTruthy();
    expect(screen.getByText('3.5.2 해상 설치 포함')).toBeTruthy();
    // 인력요청 목록 예시는 KAN-122가 생성한다. 예시가 없으면(병합 전) 빈 목록, 있으면 이 역할의 요청서만 표로 보인다.
    const listExample = tftExamples.find(item => item.path === LIST_PATH)?.body as { data: { items: StaffingRequest[] } } | undefined;
    const roleItems = (listExample?.data.items ?? []).filter(item => item.required_role_id === ROLE_ID);
    if (roleItems.length === 0) {
      expect(await screen.findByText('요청서 없음')).toBeTruthy();
      expect(screen.getByText(/프로젝트 조회 응답의 요청서 1건/)).toBeTruthy();
      expect(screen.queryByRole('form', { name: '인력 요청서 편집' })).toBeNull();
    } else {
      for (const item of roleItems) expect((await screen.findAllByText(new RegExp(item.organization_name))).length).toBeGreaterThan(0);
      expect(screen.queryByText('요청서 없음')).toBeNull();
    }
  });

  test('없는 역할은 404 안내를 보인다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, errorBody('AUTH_REQUIRED', '로그인 필요'))));
    renderApp(`/tft/projects/${PROJECT_ID}/roles/00000000-0000-4000-8000-000000000000`);
    expect(await screen.findByText('찾을 수 없습니다')).toBeTruthy();
  });

  test.each([401, 503])('로그인 확인 실패(%s) 시 편집과 발행 및 form submit을 차단한다', async status => {
    const fetchMock = stubLive([{ ...draft, due_undecided: true }], () => jsonResponse(500, {}), undefined,
      () => jsonResponse(status, errorBody('AUTH_REQUIRED', '로그인 확인 실패')));
    renderApp(`/tft/projects/${PROJECT_ID}/roles/${ROLE_ID}`);
    const form = await screen.findByRole('form', { name: '인력 요청서 편집' });
    expect(screen.getByRole('note').textContent).toContain('로그인 및 권한을 확인할 수 없습니다');
    expect((screen.getByLabelText('메시지').closest('fieldset') as HTMLFieldSetElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '발행' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(form);
    expect(fetchMock.mock.calls.filter(([, init]) => ['PATCH', 'POST'].includes(init?.method ?? ''))).toHaveLength(0);
  });

  test('로그인 조회 중에는 잠그고 sales 확인 이후에만 편집을 허용한다', async () => {
    let resolveIdentity!: (value: Response) => void;
    const identity = new Promise<Response>(resolve => { resolveIdentity = resolve; });
    stubLive([{ ...draft, due_undecided: true }], () => jsonResponse(500, {}), undefined, () => identity);
    renderApp(`/tft/projects/${PROJECT_ID}/roles/${ROLE_ID}`);
    await screen.findByRole('form', { name: '인력 요청서 편집' });
    expect((screen.getByRole('button', { name: '발행' }) as HTMLButtonElement).disabled).toBe(true);
    resolveIdentity(jsonResponse(200, { data: { user_id: 'sales-test', roles: ['sales'] } }));
    await waitFor(() => expect((screen.getByRole('button', { name: '발행' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('메시지'), { target: { value: '취소할 입력' } });
    fireEvent.click(screen.getByRole('button', { name: '서버 값으로 되돌리기' }));
    expect((screen.getByLabelText('메시지') as HTMLTextAreaElement).value).toBe('');
    expect((screen.getByRole('button', { name: '저장' }) as HTMLButtonElement).disabled).toBe(true);
  });

  test('DRAFT 요청서: 폼에 label이 연결되고 기한 미정 저장은 If-Match로 PATCH하며 412는 충돌 안내를 보인다', async () => {
    const fetchMock = stubLive([draft], () => jsonResponse(412, errorBody('VERSION_CONFLICT', '판본 불일치')));
    renderApp(`/tft/projects/${PROJECT_ID}/roles/${ROLE_ID}`);
    const form = await screen.findByRole('form', { name: '인력 요청서 편집' });
    expect(form).toBeTruthy();
    const org = screen.getByLabelText('요청 부서') as HTMLSelectElement;
    expect(org.tagName).toBe('SELECT');
    expect(Array.from(org.options).map(option => option.textContent)).toContain('합성 시공팀 (CON)');
    expect(screen.getByLabelText('참여 형태')).toBeTruthy();
    expect(screen.getByLabelText('메시지')).toBeTruthy();
    const issue = screen.getByRole('button', { name: '발행' }) as HTMLButtonElement;
    expect(issue.disabled).toBe(true);
    expect(screen.getByText(/기한을 입력하거나 "기한 미정"을 표시해야 발행/)).toBeTruthy();
    const save = screen.getByRole('button', { name: '저장' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText('기한 미정'));
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByText(/다른 사람이 먼저 수정했습니다/)).toBeTruthy();
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')!;
    expect(String(patch[0])).toBe(`/api/v1/tft/staffing-requests/${draft.id}`);
    expect((patch[1]!.headers as Record<string, string>)['If-Match']).toBe('"1"');
    // 바뀐 필드만 보낸다. due_at은 원래 null이라 그대로이고, participation·message는 바꾸지 않았다.
    expect(JSON.parse(String(patch[1]!.body))).toEqual({ due_undecided: true });
    // 충돌 후 목록을 다시 불러오되 입력값은 유지하고 서버 값을 함께 보인다.
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url) === LIST_PATH).length).toBeGreaterThan(1));
    expect((screen.getByLabelText('기한 미정') as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/서버 값 \(판본 1\)/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '서버 값으로 되돌리기' })).toBeTruthy();
  });

  test('저장 성공으로 판본이 올라가 목록이 재조회돼도 "저장했습니다" 안내와 입력값이 남는다', async () => {
    const items = [draft];
    const fetchMock = stubLive(items, init => {
      const body = JSON.parse(String(init.body)) as Partial<StaffingRequest>;
      items[0] = { ...items[0], ...body, version: 2, updated_at: '2026-10-23T02:00:00Z' };
      return jsonResponse(200, { data: items[0], meta: { request_id: '11111111-1111-4111-8111-111111111113' } });
    });
    renderApp(`/tft/projects/${PROJECT_ID}/roles/${ROLE_ID}`);
    await screen.findByRole('form', { name: '인력 요청서 편집' });
    expect(screen.getByTestId('request-version').textContent).toBe('판본 1');
    fireEvent.change(screen.getByLabelText('메시지'), { target: { value: '후보를 추천해 주세요.' } });
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    expect(await screen.findByRole('status')).toBeTruthy();
    expect(screen.getByText(/저장했습니다\. 판본 2/)).toBeTruthy();
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')!;
    expect(JSON.parse(String(patch[1]!.body))).toEqual({ message: '후보를 추천해 주세요.' });
    // 재조회가 판본 2를 돌려준 뒤에도 폼은 재마운트되지 않는다.
    await waitFor(() => expect(screen.getByTestId('request-version').textContent).toBe('판본 2'));
    expect(screen.getByRole('status').textContent).toMatch(/저장했습니다\. 판본 2/);
    expect((screen.getByLabelText('메시지') as HTMLTextAreaElement).value).toBe('후보를 추천해 주세요.');
    expect((screen.getByRole('button', { name: '저장' }) as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url) === LIST_PATH).length).toBeGreaterThan(1));
  });

  test('412 뒤 재조회로 다른 사용자가 바꾼 message는 폼에 반영되고, 다시 저장하면 사용자가 바꾼 participation만 보낸다', async () => {
    const items = [draft];
    let patchCalls = 0;
    const fetchMock = stubLive(items, init => {
      patchCalls += 1;
      if (patchCalls === 1) {
        // 다른 사용자 B가 먼저 message를 고쳐 판본 2가 됐다.
        items[0] = { ...items[0], message: 'B가 고친 메시지', version: 2 };
        return jsonResponse(412, errorBody('VERSION_CONFLICT', '판본 불일치'));
      }
      const body = JSON.parse(String(init.body)) as Partial<StaffingRequest>;
      items[0] = { ...items[0], ...body, version: 3 };
      return jsonResponse(200, { data: items[0], meta: { request_id: '11111111-1111-4111-8111-111111111114' } });
    });
    renderApp(`/tft/projects/${PROJECT_ID}/roles/${ROLE_ID}`);
    await screen.findByRole('form', { name: '인력 요청서 편집' });
    fireEvent.change(screen.getByLabelText('참여 형태'), { target: { value: 'SUPPORT' } });
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    // 재조회(판본 2)가 오면 건드리지 않은 message는 서버 값으로, 건드린 participation은 입력값으로 남는다.
    await waitFor(() => expect(screen.getByTestId('request-version').textContent).toBe('판본 2'));
    await waitFor(() => expect((screen.getByLabelText('메시지') as HTMLTextAreaElement).value).toBe('B가 고친 메시지'));
    expect((screen.getByLabelText('참여 형태') as HTMLSelectElement).value).toBe('SUPPORT');
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    await waitFor(() => expect(screen.getByTestId('request-version').textContent).toBe('판본 3'));
    const patches = fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH');
    expect(patches).toHaveLength(2);
    expect((patches[1][1]!.headers as Record<string, string>)['If-Match']).toBe('"2"');
    expect(JSON.parse(String(patches[1][1]!.body))).toEqual({ participation: 'SUPPORT' });
    expect((screen.getByLabelText('메시지') as HTMLTextAreaElement).value).toBe('B가 고친 메시지');
  });

  test('발행 재시도는 같은 판본에 같은 Idempotency-Key를 보내고 성공 후 판본이 바뀌면 새 키를 쓴다', async () => {
    const items = [{ ...draft, due_undecided: true }];
    let issueCalls = 0;
    const fetchMock = stubLive(items, () => jsonResponse(500, {}), () => {
      issueCalls += 1;
      if (issueCalls === 1) return jsonResponse(503, errorBody('DEPENDENCY_UNAVAILABLE', '일시 장애'));
      items[0] = { ...items[0], status: 'ISSUED', version: items[0].version + 1, issued_at: '2026-10-23T03:00:00Z' };
      return jsonResponse(200, { data: items[0], meta: { request_id: '11111111-1111-4111-8111-111111111115' } });
    });
    renderApp(`/tft/projects/${PROJECT_ID}/roles/${ROLE_ID}`);
    await screen.findByRole('form', { name: '인력 요청서 편집' });
    const issue = screen.getByRole('button', { name: '발행' }) as HTMLButtonElement;
    expect(issue.disabled).toBe(false);
    fireEvent.click(issue);
    expect(await screen.findByText('요청을 처리하지 못했습니다')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '발행' }));
    expect(await screen.findByText(/요청서를 발행했습니다/)).toBeTruthy();
    const posts = fetchMock.mock.calls.filter(([url, init]) => init?.method === 'POST' && String(url).endsWith('/issue'));
    expect(posts).toHaveLength(2);
    const keys = posts.map(([, init]) => (init!.headers as Record<string, string>)['Idempotency-Key']);
    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(keys[1]).toBe(keys[0]);
    // 발행 뒤에는 읽기 전용으로 잠긴다.
    await waitFor(() => expect(screen.getByRole('note').textContent).toMatch(/현재 상태: 발행/));
  });

  test('DRAFT가 아닌 요청서는 폼을 읽기 전용으로 잠그고 이유를 보인다', async () => {
    stubLive([{ ...draft, status: 'ISSUED', version: 2, issued_at: '2026-10-23T01:00:00Z', due_undecided: true }], () => jsonResponse(500, {}));
    renderApp(`/tft/projects/${PROJECT_ID}/roles/${ROLE_ID}`);
    await screen.findByRole('form', { name: '인력 요청서 편집' });
    expect(screen.getByRole('note').textContent).toMatch(/작성 중\(DRAFT\) 요청서만 편집·발행할 수 있습니다\. 현재 상태: 발행/);
    // 폼 전체를 fieldset으로 잠근다. 개별 컨트롤의 disabled 속성이 아니라 조상 fieldset을 본다.
    expect((screen.getByLabelText('메시지').closest('fieldset') as HTMLFieldSetElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '발행' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '저장' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
