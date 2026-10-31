// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderApp } from '../test/render';

const project = (id: string, name: string, output_status = 'ANALYZING', extra: Record<string, unknown> = {}) => ({
  id, name, status: 'COMPOSING', external_id: 'P26-0001', company_code: 'HOST', data_origin: 'synthetic', derivation_kind: 'ASSUMED',
  responsible_user_id: null, sales_user_id: 's', created_at: '2026-10-20T09:00:00Z', known_at: '2026-10-20T09:00:00Z', version: 1,
  client_name: '가상 고객', output_status, judgement_status: 'CONDITION_CHECK', requirements_count: 48, unresolved_mandatory_count: 3,
  pending_proposals_count: 4, open_questions_count: 2, strategic_customer: null, integration_responsibility: { holder: 'UNRESOLVED' }, ...extra,
});
const isList = (url: string) => /\/tft\/projects(\?|$)/.test(url);
const page = (items: unknown[], next_cursor: string | null = null) =>
  jsonResponse(200, { data: { items, next_cursor }, meta: { request_id: 'x' } });

afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); setApiMode('mock'); });

// 개발 모드라 권한 관문은 열린 채로 실제 API 응답만 바꾼다.
const live = (respond: (url: string) => Response) => {
  const f = vi.fn(async (input: RequestInfo | URL) => respond(String(input)));
  vi.stubGlobal('fetch', f);
  setApiMode('live');
  return f;
};

describe('프로젝트 목록', () => {
  it('목록을 셸 안에 그리고 상세로 잇는다', async () => {
    renderApp('/tft/projects');
    const link = await screen.findByRole('link', { name: /해저케이블/ });
    const table = screen.getByRole('table', { name: '접수 및 요구 검토' });
    expect(link.getAttribute('href')).toBe('/tft/projects/33333333-3333-4333-8333-333333333333');
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent)).toEqual(
      ['', 'No', '프로젝트', '프로젝트명', '고객', '진행', '판단', '요구', '미해결 필수', '승인 대기', '열린 질문', '담당', '책임자', '접수일'],
    );
    expect(within(table).getAllByText('접수').length).toBeGreaterThan(0);
    expect(document.querySelector('.ls-root .ls-legacy')).toBeNull();
    expect(table.classList.contains('free')).toBe(true); // C/S 목록의 앞 세 열 고정을 쓰지 않는다
    expect(screen.getByRole('tab', { selected: true }).textContent).toContain('접수 및 요구 검토');
  });

  it('커서로 다음 쪽을 부르고 이전 쪽으로 돌아온다', async () => {
    const f = live(url => url.includes('cursor=c%2F2') ? page([project('2', '둘째 쪽')]) : isList(url) ? page([project('1', '첫째 쪽')], 'c/2') : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft/projects');
    await screen.findByText('첫째 쪽');
    fireEvent.click(screen.getByRole('button', { name: '다음' }));
    await screen.findByText('둘째 쪽');
    expect(f.mock.calls.some(([u]) => String(u).endsWith('/api/v1/tft/projects?cursor=c%2F2'))).toBe(true);
    await waitFor(() => expect((screen.getByRole('button', { name: '다음' }) as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: '이전' }));
    await screen.findByText('첫째 쪽');
  });

  it('빈 목록과 오류를 구분한다', async () => {
    live(url => url.includes('/projects') ? page([]) : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft/projects');
    await screen.findByText('프로젝트가 없습니다.');
    cleanup();
    live(url => url.includes('/projects') ? jsonResponse(500, errorBody('TFT_X', '실패')) : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft/projects');
    expect((await screen.findByRole('alert')).textContent).toContain('실패');
    expect(screen.queryByText('프로젝트가 없습니다.')).toBeNull();
  });

  it('업무 UI가 꺼졌으면 프로젝트를 부르지 않는다', async () => {
    vi.stubEnv('DEV', false);
    const f = live(() => jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft/projects');
    await waitFor(() => expect(document.querySelector('.ls-root')).not.toBeNull());
    expect(f.mock.calls.some(([u]) => String(u).includes('/tft/projects'))).toBe(false);
    expect(screen.queryByRole('table')).toBeNull();
  });
});

describe('프로젝트 조회조건', () => {
  const server = () => live(url => isList(url) ? page([project('1', '합성')]) : jsonResponse(401, errorBody('X', '세션')));

  it('진행·판단·검색어를 서버로 보내고 첫 쪽으로 돌아간다', async () => {
    const f = server();
    renderApp('/tft/projects');
    await screen.findByText('합성');
    fireEvent.change(screen.getByLabelText('진행'), { target: { value: 'INTAKE' } });
    fireEvent.change(screen.getByLabelText('판단'), { target: { value: 'NEEDS_REVIEW' } });
    fireEvent.change(screen.getByLabelText('검색어'), { target: { value: ' 변전 ' } });
    fireEvent.submit(screen.getByRole('form', { name: '조회조건' }));
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u).includes('output_status=INTAKE') && String(u).includes('judgement_status=NEEDS_REVIEW') && decodeURIComponent(String(u)).includes('q=변전'))).toBe(true));
    expect(screen.getByText('합성')).toBeTruthy(); // 새 응답을 기다리는 동안에도 표가 남는다
  });

  it('한 글자 검색어는 보내지 않고 알린다', async () => {
    const f = server();
    renderApp('/tft/projects');
    await screen.findByText('합성');
    fireEvent.change(screen.getByLabelText('검색어'), { target: { value: '변' } });
    fireEvent.submit(screen.getByRole('form', { name: '조회조건' }));
    expect(screen.getByText('검색어는 두 글자 이상 입력하세요.')).toBeTruthy();
    expect((screen.getByRole('button', { name: '조회' }) as HTMLButtonElement).disabled).toBe(true);
    expect(f.mock.calls.some(([u]) => String(u).includes('q='))).toBe(false);
  });

  it('요구가 없는 접수 단계는 건수를 0으로 보이지 않는다', async () => {
    live(url => url.includes('/tft/projects') ? page([project('1', '합성', 'INTAKE', { requirements_count: 0, pending_proposals_count: 0, open_questions_count: 0, unresolved_mandatory_count: 0 })]) : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft/projects');
    const row = (await screen.findByText('합성')).closest('tr')!;
    expect(within(row).getByText('없음')).toBeTruthy();
    expect(within(row).queryByText('0')).toBeNull();
  });
});

describe('업무 홈', () => {
  it('받아 온 목록을 상태별로 센다', async () => {
    live(url => url.includes('/projects') ? page([project('1', '가'), project('2', '나', 'ANALYZING', { requirements_count: 10, pending_proposals_count: 0, open_questions_count: 0, unresolved_mandatory_count: 0 }), project('3', '다', 'INTAKE', { requirements_count: 0, pending_proposals_count: 0, open_questions_count: 0, unresolved_mandatory_count: 0 })]) : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft');
    await screen.findByRole('link', { name: '가' });
    expect(screen.getByText(/첫 쪽 기준/)).toBeTruthy();
    const steps = document.querySelector('.kv-steps')!.textContent!;
    expect(steps).toContain('분석·검토 중 2');
    expect(steps).toContain('접수 1');
    expect(steps).toContain('검토본 작성 0');
    expect(document.querySelector('.kv-next')?.textContent).toContain('내 프로젝트 3, 할 일 있는 프로젝트 2 | 미해결 필수 3, 승인 대기 4, 열린 질문 2');
    const table = screen.getByRole('table', { name: '내 프로젝트' });
    expect(within(table).getByText('승인 대기 4건 처리, 열린 질문 2건 회신 기록, 미해결 필수 3건 판단')).toBeTruthy();
    expect(within(table).getByText('없음')).toBeTruthy();
    expect(within(table).getByText('문서 올리기, 처리 실행')).toBeTruthy();
  });

  it('조회 전이나 실패 때 0건으로 보이지 않는다', async () => {
    live(url => url.includes('/projects') ? jsonResponse(500, errorBody('TFT_X', '실패')) : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft');
    await screen.findByRole('alert');
    expect(document.querySelector('.kv-steps')!.textContent).not.toMatch(/\d/);
  });
});
