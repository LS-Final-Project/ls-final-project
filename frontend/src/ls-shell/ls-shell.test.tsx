// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse } from '../test/render';

const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf-8');
const show = (path: string) => render(
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
  </QueryClientProvider>,
);

afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); setApiMode('mock'); sessionStorage.clear(); });

describe('업무 셸과 옛 화면의 스타일 경계', () => {
  it('@scope 미지원 브라우저에서 전역 규칙이 사라지지 않는다', () => {
    expect(read('../style.css')).not.toContain('@scope');
  });

  it('셸 초기화는 옛 화면 영역을 비껴 간다', () => {
    expect(read('./ls-shell.css')).toMatch(/\.ls-root :where\([^)]*\):where\(:not\(\.ls-legacy \*\)\)\{all:revert\}/);
  });

  it.each(['/cs/improvements'])('%s 는 셸 안의 옛 화면 영역에 그린다', path => {
    const { container } = show(path);
    expect(container.querySelector('.ls-root .ls-legacy')).not.toBeNull();
  });

  it('문의 처리는 보드 기준 새 화면이라 옛 화면 영역이 아니다', () => {
    const { container } = show('/cs/tickets/22222222-2222-4222-8222-222222222222');
    expect(container.querySelector('.ls-legacy .kv-code')).toBeNull();
    expect(container.querySelector('.kv-code')?.textContent).toBe('CS0102');
  });

  it('문의 조회는 보드 기준 새 화면이라 옛 화면 영역이 아니다', () => {
    const { container } = show('/cs/tickets');
    expect(container.querySelector('.ls-legacy')).toBeNull();
    expect(container.querySelector('.kv-code')?.textContent).toBe('CS0101');
  });

  it('업무 홈은 옛 화면 영역이 아니다', () => {
    const { container } = show('/cs');
    expect(container.querySelector('.ls-root')).not.toBeNull();
    expect(container.querySelector('.ls-legacy')).toBeNull();
  });
});

describe('셸 메뉴', () => {
  const live = (status: number) => {
    vi.stubEnv('DEV', false);
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(status, errorBody('X', '세션'))));
    setApiMode('live');
  };
  const hrefs = (container: HTMLElement) =>
    [...container.querySelectorAll('.sidebar a[href]')].map(a => a.getAttribute('href'));

  it('개발 환경에서는 포털의 다른 업무 화면으로 갈 수 있다', async () => {
    const { container } = show('/cs');
    await waitFor(() => expect(hrefs(container)).toContain('/cs/tickets'));
    expect(hrefs(container)).toEqual(expect.arrayContaining(['/', '/cs', '/account']));
    screen.getByRole('button', { name: '제안 지원' }).click();
    await waitFor(() => expect(hrefs(container)).toContain('/tft/projects'));
    screen.getByRole('button', { name: '시스템 관리' }).click();
    await waitFor(() => expect(hrefs(container)).toContain('/evaluations'));
  });

  it('업무 UI가 꺼졌거나 미로그인이면 업무 경로를 열지 않는다(Layout과 같은 조건)', async () => {
    live(401);
    const { container } = show('/cs');
    await screen.findByText('로그인 필요');
    expect(hrefs(container)).not.toContain('/cs/tickets');
    expect(hrefs(container)).toContain('/account');
    fireEvent.click(screen.getByRole('button', { name: '시스템 관리' }));
    expect(screen.queryByText('평가')).toBeNull();
  });

  it.each([[true, false], [false, true]])('좁은 화면 %s: 화면을 옮기면 메뉴를 닫고, 넓은 화면은 접기를 유지한다', async (narrow, kept) => {
    vi.stubGlobal('matchMedia', () => ({ matches: narrow }));
    const { container } = show('/cs');
    const shell = () => container.querySelector('#shell')!;
    expect(shell().classList.contains('menu-open')).toBe(false);
    fireEvent.click(container.querySelector('#nav-collapse')!);
    expect(shell().classList.contains('menu-open')).toBe(true);
    fireEvent.click(within(screen.getByRole('navigation', { name: '메뉴' })).getByRole('link', { name: '문의 조회' }));
    await waitFor(() => expect(shell().classList.contains('menu-open')).toBe(kept));
  });

  it.each([
    ['/tft/projects/p1/requirements/r1', '접수 및 요구 검토'],
    ['/tft/projects/p1', '접수 및 요구 검토'],
    ['/tft/projects/p1/table', '자사 대응표'],
  ])('%s 에서 메뉴는 %s 만 강조한다', async (path, name) => {
    const { container } = show(path);
    await waitFor(() => expect([...container.querySelectorAll('.sidebar a[aria-current="page"]')].map(a => a.textContent)).toEqual([name]));
  });

  it('연 화면이 탭으로 쌓이고 누르면 그 화면으로 간다', async () => {
    const { container } = show('/tft/projects');
    await screen.findByRole('tab', { name: '접수 및 요구 검토', selected: true });
    fireEvent.click(within(screen.getByRole('navigation', { name: '메뉴' })).getByRole('link', { name: '업무 홈' }));
    await screen.findByRole('tab', { name: '업무 홈', selected: true });
    expect(screen.getByRole('tab', { name: '접수 및 요구 검토', selected: false })).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: '접수 및 요구 검토' }));
    await screen.findByRole('tab', { name: '접수 및 요구 검토', selected: true });
    expect(container.querySelectorAll('.kt-tab')).toHaveLength(2);
  });

  it('업무 홈 탭은 닫을 수 없고 현재 탭을 닫으면 앞 탭으로 간다', async () => {
    show('/tft');
    await screen.findByRole('tab', { name: '업무 홈', selected: true });
    expect(screen.queryByRole('button', { name: '업무 홈 닫기' })).toBeNull();
    fireEvent.click(within(screen.getByRole('navigation', { name: '메뉴' })).getByRole('link', { name: '접수 및 요구 검토' }));
    await screen.findByRole('tab', { name: '접수 및 요구 검토', selected: true });
    fireEvent.click(screen.getByRole('button', { name: '접수 및 요구 검토 닫기' }));
    await screen.findByRole('tab', { name: '업무 홈', selected: true });
    await waitFor(() => expect(screen.queryByRole('tab', { name: '접수 및 요구 검토' })).toBeNull());
  });

  it('탭은 새로고침해도 남고 저장소를 못 써도 셸이 돈다', async () => {
    sessionStorage.setItem('ls-open-tabs', JSON.stringify([{ kind: 'table', to: '/tft/projects/p1/table', name: '자사 대응표' }]));
    show('/tft');
    await screen.findByRole('tab', { name: '자사 대응표', selected: false });
    cleanup();
    sessionStorage.clear();
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('막힘'); });
    show('/tft');
    await screen.findByRole('tab', { name: '업무 홈', selected: true });
    spy.mockRestore();
  });

  it('세션 장애를 로그아웃으로 표시하지 않는다', async () => {
    live(500);
    show('/cs');
    await screen.findByText('세션 확인 실패');
  });
});
