// @vitest-environment jsdom
import React from 'react';
import { afterEach, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { DocumentVersion } from '../api/rfp';
import { RfpReasoning } from './RfpReasoning';

afterEach(cleanup);
const labels = { extract: '요구 추출', draft: '초안 작성' };
const event = { sequence: 4, occurredAt: '2026-11-03T10:00:00Z', type: 'job.progress', step: 'extract', counts: { requirements: 5, proposals: 0 }, documentVersionId: 'version-1' };
const documents = [{ document_version_id: 'version-1', title: '합성 단선도', version: 2 }] as DocumentVersion[];
it('shows only public observations, the actual document and provisional counts', () => {
  const { container } = render(<RfpReasoning activity={[event]} active completed={false} disconnected={false} documents={documents} labels={labels} />);
  const toggle = screen.getByRole('button', { name: /AI 작업 내역/ });
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  expect(container.querySelector('.is-active')).toBeTruthy();
  expect(screen.getByText('관측 문서: 합성 단선도 v2')).toBeTruthy();
  expect(screen.getByText('요구 5건 · 제안 0건 발견 (임시)')).toBeTruthy();
  expect(screen.queryByText(/문서 읽기/)).toBeNull();
  fireEvent.click(toggle);
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(document.getElementById(toggle.getAttribute('aria-controls')!)?.getAttribute('aria-hidden')).toBe('true');
});
it('removes the live shimmer while reconnecting and after completion without collapsing records', () => {
  const ui = (active: boolean, completed: boolean, disconnected: boolean) => <RfpReasoning activity={[event]} active={active} completed={completed} disconnected={disconnected} documents={[]} labels={labels} />;
  const { container, rerender } = render(ui(true, false, false));
  rerender(ui(false, false, true));
  expect(container.querySelector('.is-active')).toBeNull();
  expect(screen.getByText(/연결을 다시 확인/)).toBeTruthy();
  expect(screen.getByText(/관측 문서: 문서 판본 이름 확인 중/)).toBeTruthy();
  rerender(ui(false, true, false));
  expect(container.querySelector('.is-active')).toBeNull();
  expect(screen.getByRole('button', { name: /AI 작업 내역/ }).getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByText(/처리 결과와 함께 작업 기록/)).toBeTruthy();
});
