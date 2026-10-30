// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import App from '../App';

const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf-8');
const show = (path: string) => render(
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
  </QueryClientProvider>,
);

describe('업무 셸과 옛 화면의 스타일 경계', () => {
  it('@scope 미지원 브라우저에서 전역 규칙이 사라지지 않는다', () => {
    expect(read('../style.css')).not.toContain('@scope');
  });

  it('셸 초기화는 옛 화면 영역을 비껴 간다', () => {
    expect(read('./ls-shell.css')).toMatch(/\.ls-root :where\([^)]*\):where\(:not\(\.ls-legacy \*\)\)\{all:revert\}/);
  });

  it.each(['/cs/tickets', '/cs/tickets/22222222-2222-4222-8222-222222222222'])('%s 는 셸 안의 옛 화면 영역에 그린다', path => {
    const { container } = show(path);
    expect(container.querySelector('.ls-root .ls-legacy')).not.toBeNull();
  });

  it('업무 홈은 옛 화면 영역이 아니다', () => {
    const { container } = show('/cs');
    expect(container.querySelector('.ls-root')).not.toBeNull();
    expect(container.querySelector('.ls-legacy')).toBeNull();
  });
});
