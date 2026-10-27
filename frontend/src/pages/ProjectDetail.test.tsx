import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, screen, within } from '@testing-library/react';
import tftExamples from '../mocks/generated/tft.json';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderApp } from '../test/render';

const PROJECT_ID = '33333333-3333-4333-8333-333333333333';
const PROJECT_PATH = `/api/v1/tft/projects/${PROJECT_ID}`;
const example = tftExamples.find(item => item.path === PROJECT_PATH)!.body;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

describe('S1 프로젝트·역할 현황', () => {
  test('모의 응답으로 기본 정보·문서처리 준비 중·초기검토·필수/우대/미정·원문 근거·역할 링크를 그린다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, errorBody('AUTH_REQUIRED', '로그인 필요'))));
    renderApp(`/tft/projects/${PROJECT_ID}`);
    expect(await screen.findByRole('heading', { level: 1, name: /해저케이블/ })).toBeTruthy();
    expect(screen.getByText('지정됨', { exact: false })).toBeTruthy();
    expect(screen.getByText('문서 처리 연결 준비 중')).toBeTruthy();
    expect(screen.getByRole('heading', { name: '초기검토' })).toBeTruthy();
    expect(screen.getByText('초기검토 이후 단계', { exact: false })).toBeTruthy();
    // R-01은 필수·우대 역량이 있는 시공 역할에, R-02는 역량이 없는 설계 역할에 연결돼 있다.
    expect(screen.getByText(/^필수 1 · 우대 0 · 미정 1/)).toBeTruthy();
    const r01 = screen.getByText('R-01').closest('tr')!;
    expect(within(r01).getByText('필수')).toBeTruthy();
    expect(within(r01).getByText('The supplier shall perform marine installation and burial.')).toBeTruthy();
    expect(within(r01).getByText('§3.5.2')).toBeTruthy();
    const r02 = screen.getByText('R-02').closest('tr')!;
    expect(within(r02).getByText('미정')).toBeTruthy();
    expect(within(r02).getByText('발췌 없음')).toBeTruthy();
    const roleLink = screen.getAllByRole('link', { name: '해상 시공 관리' })[0] as HTMLAnchorElement;
    expect(roleLink.getAttribute('href')).toBe(`/tft/projects/${PROJECT_ID}/roles/88888888-8888-4888-8888-888888888802`);
    expect(screen.getByText('부서 검증')).toBeTruthy();
  });

  test('404는 재시도 없이 안내만 한다', async () => {
    setApiMode('live');
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(404, errorBody('TFT_PROJECT_NOT_FOUND', '없는 프로젝트'))));
    renderApp('/tft/projects/00000000-0000-4000-8000-000000000000');
    expect(await screen.findByText('찾을 수 없습니다')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '다시 시도' })).toBeNull();
  });

  test('요구·역할이 없고 책임자가 없으면 Preparing·Empty·미지정 안내를 보인다', async () => {
    setApiMode('live');
    const body = structuredClone(example) as typeof example & { data: Record<string, unknown> };
    Object.assign(body.data, { rfp_requirements: [], required_roles: [], staffing_requests: [], responsible: null, responsible_user_id: null, status: 'INITIAL_REVIEW' });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input) === PROJECT_PATH ? jsonResponse(200, body) : jsonResponse(401, errorBody('AUTH_REQUIRED', '로그인 필요'))));
    renderApp(`/tft/projects/${PROJECT_ID}`);
    expect(await screen.findByText(/문서 처리 연결 준비 중/)).toBeTruthy();
    expect(screen.getByText('아직 정리된 역할이 없습니다.')).toBeTruthy();
    expect(screen.getByText(/책임자 미지정/)).toBeTruthy();
    expect(screen.getByText('초기검토 진행 중')).toBeTruthy();
  });
});
