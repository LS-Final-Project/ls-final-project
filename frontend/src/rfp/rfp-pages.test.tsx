// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderApp } from '../test/render';
import { dueText, progressOf } from './RfpList';

const project = (id: string, name: string, output_status = 'ANALYZING', extra: Record<string, unknown> = {}) => ({
  id, name, status: 'COMPOSING', external_id: 'P26-0001', company_code: 'HOST', data_origin: 'synthetic', derivation_kind: 'ASSUMED',
  responsible_user_id: null, sales_user_id: 's', created_at: '2026-10-20T09:00:00Z', known_at: '2026-10-20T09:00:00Z', version: 1,
  client_name: '가상 고객', output_status, judgement_status: 'CONDITION_CHECK', requirements_count: 48, unresolved_mandatory_count: 3,
  pending_proposals_count: 4, open_questions_count: 2, strategic_customer: null, integration_responsibility: { holder: 'UNRESOLVED' },
  decisions_remaining: 21, review_pending: 1, submission_due_at: '2099-11-20', ...extra,
});
const isList = (url: string) => /\/tft\/projects(\?|$)/.test(url);
const page = (items: unknown[], next_cursor: string | null = null, total = next_cursor ? 20 : items.length) =>
  jsonResponse(200, { data: { items, next_cursor, total }, meta: { request_id: 'x' } });

afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); setApiMode('mock'); });

// 개발 모드라 권한 관문은 열린 채로 실제 API 응답만 바꾼다.
const live = (respond: (url: string) => Response) => {
  const f = vi.fn(async (input: RequestInfo | URL) => respond(String(input)));
  vi.stubGlobal('fetch', f);
  setApiMode('live');
  return f;
};

describe('RFP 검토 목록 (KAN-222)', () => {
  it('다섯 열과 새 RFP를 셸 안에 그리고 작업 공간으로 잇는다', async () => {
    live(url => isList(url) ? page([project('33333333-3333-4333-8333-333333333333', '해저케이블')]) : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft/projects');
    const link = await screen.findByRole('link', { name: /해저케이블/ });
    expect(link.getAttribute('href')).toBe('/tft/projects/33333333-3333-4333-8333-333333333333');
    const table = screen.getByRole('table', { name: 'RFP 검토' });
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent)).toEqual(['No', '프로젝트', '진행 상태', '제안 마감', '책임자', '다음 행동']);
    expect(within(table).getByText('결정 21건 남음')).toBeTruthy();
    expect(within(table).getByText(/^2099-11-20 \(\d+일 남음\)$/)).toBeTruthy();
    expect(within(table).getByRole('link', { name: '결정 이어서 하기' })).toBeTruthy();
    expect(screen.getByRole('link', { name: '새 RFP' }).getAttribute('href')).toBe('/tft/projects/new');
    expect(screen.getByRole('tab', { selected: true }).textContent).toContain('RFP 검토');
  });

  it('요구가 없으면 분석 대기와 분석 시작, 미해결이 없으면 출력 가능', async () => {
    live(url => isList(url) ? page([
      project('1', '가', 'INTAKE', { requirements_count: 0, unresolved_mandatory_count: 0 }),
      project('2', '나', 'ANALYZING', { unresolved_mandatory_count: 0, decisions_remaining: 0 }),
    ]) : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft/projects');
    const first = (await screen.findByRole('link', { name: /가/ })).closest('tr')!;
    expect(within(first).getByText('분석 대기')).toBeTruthy();
    expect(within(first).getByRole('link', { name: '분석 시작' })).toBeTruthy();
    const second = screen.getByRole('link', { name: /나/ }).closest('tr')!;
    expect(within(second).getByText('출력 가능')).toBeTruthy();
  });

  it('번호로 다음 쪽을 부르고 검색어를 서버로 보낸다', async () => {
    const f = live(url => url.includes('page=2') ? page([project('2', '둘째 쪽')], null, 11) : isList(url) ? page([project('1', '첫째 쪽')], 'c/2') : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft/projects');
    await screen.findByText(/첫째 쪽/);
    fireEvent.click(screen.getByRole('button', { name: '다음' }));
    await screen.findByText(/둘째 쪽/);
    expect(f.mock.calls.some(([u]) => String(u).includes('page=2&limit=10'))).toBe(true);
    fireEvent.change(screen.getByLabelText('검색어'), { target: { value: ' 변전 ' } });
    fireEvent.submit(screen.getByRole('form', { name: '프로젝트 검색' }));
    await waitFor(() => expect(f.mock.calls.some(([u]) => decodeURIComponent(String(u)).includes('q=변전'))).toBe(true));
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

describe('업무 홈 (KAN-222)', () => {
  it('프로젝트를 넘나드는 오늘 할 일만 보여 준다', async () => {
    live(url => url.includes('/projects') ? page([project('1', '가'), project('2', '나', 'ANALYZING', { requirements_count: 10, pending_proposals_count: 0, open_questions_count: 0, unresolved_mandatory_count: 0, decisions_remaining: 0 }), project('3', '다', 'INTAKE', { requirements_count: 0 })]) : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft');
    await screen.findAllByText(/^결정 21건 남음 \(마감 \d+일 전\)$/);
    expect(screen.getByRole('table', { name: '최근 프로젝트' })).toBeTruthy();
    expect(screen.getByText('회신 기다리는 질의 2건')).toBeTruthy();
    expect(screen.getByText('승인 대기 4건')).toBeTruthy();
    expect(screen.getAllByText('분석 시작 전').length).toBe(1);
    expect(screen.queryByText(/용어 풀이/)).toBeNull();
    expect(screen.queryByRole('table', { name: '담당자별 진행' })).toBeNull();
  });

  it('조회 실패를 0건으로 보이지 않는다', async () => {
    live(url => url.includes('/projects') ? jsonResponse(500, errorBody('TFT_X', '실패')) : jsonResponse(401, errorBody('X', '세션')));
    renderApp('/tft');
    await screen.findByRole('alert');
    expect(screen.queryByText(/오늘 할 일/)).toBeNull();
  });
});

it('책임자 진행 상태에는 확인 대기를 붙이고, 마감은 한국 날짜로 남은 날수를 센다', () => {
  const p = project('1', '가') as never;
  expect(progressOf(p, true)).toEqual({ text: '결정 21건 남음, 확인 대기 1', action: '확인하기' });
  expect(progressOf(p)).toEqual({ text: '결정 21건 남음', action: '결정 이어서 하기' });
  const now = new Date('2026-11-04T16:00:00Z'); // 한국 11월 5일 01시
  expect(dueText('2026-11-20', now)).toBe('2026-11-20 (15일 남음)');
  expect(dueText('2026-11-05', now)).toBe('2026-11-05 (오늘)');
  expect(dueText(null, now)).toBe('─');
});
