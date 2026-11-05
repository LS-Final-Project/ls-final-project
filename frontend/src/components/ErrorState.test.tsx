import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ApiError } from '../api/client';
import { ErrorState, InShell } from './states';

afterEach(cleanup);
const inShell = (node: React.ReactNode) => render(<MemoryRouter><InShell.Provider value>{node}</InShell.Provider></MemoryRouter>);

test('셸 안 403은 오류 화면 보드 모양으로 상태 표시와 업무 홈 이동을 보이고 다시 시도는 숨긴다', () => {
  const retry = vi.fn();
  const { container } = inShell(<ErrorState error={new ApiError(403, '금지', 'req-1')} onRetry={retry} />);
  expect(container.querySelector('.kv-err')).not.toBeNull();
  expect(screen.getByRole('alert').textContent).toContain('권한이 없습니다');
  expect(container.querySelector('.kv-err h2 .auto')?.textContent).toBe('403');
  expect(screen.getByRole('link', { name: '업무 홈으로' }).getAttribute('href')).toBe('/cs');
  expect(screen.queryByRole('button', { name: '다시 시도' })).toBeNull();
  expect(screen.getByText('요청 ID req-1')).toBeTruthy();
});

test('셸 안에서 응답 없는 실패는 연결 안 됨으로 알리고 다시 시도할 수 있다', () => {
  const retry = vi.fn();
  const { container } = inShell(<ErrorState error={new TypeError('Failed to fetch')} onRetry={retry} />);
  expect(screen.getByRole('alert').textContent).toContain('업무 서버에 연결할 수 없습니다');
  expect(container.querySelector('.kv-err h2 .auto')?.textContent).toBe('연결 안 됨');
  expect(screen.getByText(/입력 중이던 내용은 이 화면에 남아 있습니다/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '다시 시도' })); expect(retry).toHaveBeenCalledTimes(1);
});

test('셸 안 5xx는 다시 시도와 오류 번호 복사를 보이고 요청 ID를 적는다', () => {
  const writeText = vi.fn(async () => undefined);
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
  inShell(<ErrorState error={new ApiError(503, '일시 장애', 'req-503')} onRetry={() => undefined} />);
  expect(screen.getByRole('button', { name: '다시 시도' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '오류 번호 복사' }));
  expect(writeText).toHaveBeenCalledWith('req-503');
  expect(screen.getByText('요청 ID req-503')).toBeTruthy();
  vi.unstubAllGlobals();
});

test('셸 밖(옛 화면)은 이전 모양 그대로다', () => {
  const { container } = render(<MemoryRouter><ErrorState error={new ApiError(503, '일시 장애', 'req-9')} onRetry={() => undefined} /></MemoryRouter>);
  expect(container.querySelector('.kv-err')).toBeNull();
  expect(container.querySelector('.state.error')).not.toBeNull();
  expect(screen.getByRole('button', { name: '다시 시도' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: '오류 번호 복사' })).toBeNull();
});
