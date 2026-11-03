// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation, useParams } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import App from '../App';

vi.mock('../ls-shell/LsShell', async () => {
  const { Outlet } = await import('react-router-dom');
  return { default: () => <Outlet /> };
});
function Review() { const location = useLocation(); const { projectId } = useParams(); return <div data-testid="review">{projectId}:{location.pathname}{location.search}{location.hash}</div>; }
vi.mock('./RfpIntake', () => ({ default: () => <Review /> }));
vi.mock('./RfpApprovals', () => ({ default: () => <p>기존 승인 화면</p> }));
vi.mock('./RfpRequirement', () => ({ default: () => <p>기존 요구 수정 화면</p> }));
afterEach(cleanup);
function show(path: string) { return render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>); }
it.each(['table','supplement'])('기존 %s 링크는 같은 프로젝트 검토로 연결하고 검색조건을 보존한다', async page => {
  show(`/tft/projects/project-a/${page}?requirement=r-1`);
  expect((await screen.findByTestId('review')).textContent).toBe('project-a:/tft/projects/project-a?requirement=r-1#rfp-document-review');
});
it('기존 링크의 명시한 문서 위치를 보존한다', async () => {
  show('/tft/projects/project-b/table#source');
  expect((await screen.findByTestId('review')).textContent).toBe('project-b:/tft/projects/project-b#source');
});
it.each([['table/approvals','기존 승인 화면'],['requirements/r1','기존 요구 수정 화면']])('%s 의 독립 편집·승인 경로를 가로채지 않는다', async (path, text) => {
  show(`/tft/projects/project-a/${path}`);
  expect(await screen.findByText(text)).toBeTruthy();
  expect(screen.queryByTestId('review')).toBeNull();
});
