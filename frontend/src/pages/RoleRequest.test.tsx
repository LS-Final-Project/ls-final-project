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

function stubLive(items: StaffingRequest[], onPatch: () => Response) {
  setApiMode('live');
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === PROJECT_PATH) return jsonResponse(200, project);
    if (url === LIST_PATH) return jsonResponse(200, { data: { items }, meta: { request_id: '11111111-1111-4111-8111-111111111112' } });
    if (init?.method === 'PATCH') return onPatch();
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
    expect(JSON.parse(String(patch[1]!.body))).toMatchObject({ due_undecided: true, due_at: null, participation: 'LEAD' });
    // 충돌 후 목록을 다시 불러온다.
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url) === LIST_PATH).length).toBeGreaterThan(1));
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
