import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, screen, within } from '@testing-library/react';
import tftExamples from '../mocks/generated/tft.json';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderApp } from '../test/render';

const PROJECT_ID = '33333333-3333-4333-8333-333333333333';
const PROJECT_PATH = `/api/v1/tft/projects/${PROJECT_ID}`;
const example = tftExamples.find(item => item.path === PROJECT_PATH)!.body;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

describe('프로젝트 현황', () => {
  test('모의 응답으로 기본 정보·문서처리 준비 중·RFP 요구사항과 원문 근거를 그리고 제거된 인력 구성 절은 없다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, errorBody('AUTH_REQUIRED', '로그인 필요'))));
    renderApp(`/tft/projects/${PROJECT_ID}`);
    expect(await screen.findByRole('heading', { level: 1, name: /해저케이블/ })).toBeTruthy();
    expect(screen.getByText('문서 처리 연결 준비 중')).toBeTruthy();
    const r01 = screen.getByText('R-01').closest('tr')!;
    expect(within(r01).getByText('The supplier shall perform marine installation and burial.')).toBeTruthy();
    expect(within(r01).getByText('§3.5.2')).toBeTruthy();
    const r02 = screen.getByText('R-02').closest('tr')!;
    expect(within(r02).getByText('발췌 없음')).toBeTruthy();
    for (const name of ['초기검토', '역할 현황']) expect(screen.queryByRole('heading', { name })).toBeNull();
    expect(screen.queryByRole('link', { name: /구성안/ })).toBeNull();
  });

  test('404는 재시도 없이 안내만 한다', async () => {
    setApiMode('live');
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(404, errorBody('TFT_PROJECT_NOT_FOUND', '없는 프로젝트'))));
    renderApp('/tft/projects/00000000-0000-4000-8000-000000000000');
    expect(await screen.findByText('찾을 수 없습니다')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '다시 시도' })).toBeNull();
  });

  test('요구사항이 없고 책임자가 없으면 Preparing·Empty·미지정을 보인다', async () => {
    setApiMode('live');
    const body = structuredClone(example) as typeof example & { data: Record<string, unknown> };
    Object.assign(body.data, { rfp_requirements: [], responsible_user_id: null });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input) === PROJECT_PATH ? jsonResponse(200, body) : jsonResponse(401, errorBody('AUTH_REQUIRED', '로그인 필요'))));
    renderApp(`/tft/projects/${PROJECT_ID}`);
    expect(await screen.findByText(/문서 처리 연결 준비 중/)).toBeTruthy();
    expect(screen.getByText('정리된 요구사항이 없습니다.')).toBeTruthy();
    expect(screen.getAllByText('미지정').length).toBeGreaterThanOrEqual(1);
  });
});
