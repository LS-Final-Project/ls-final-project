// @vitest-environment jsdom
import { act, cleanup, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setApiMode } from '../api/client';
import { renderAt } from '../test/render';
import RfpApprovals from './RfpApprovals';
import RfpHome from './RfpHome';
import RfpIntake from './RfpIntake';
import RfpRequirement from './RfpRequirement';
import RfpTable from './RfpTable';

// 운영 빌드는 권한 확인이 끝나야 화면이 열린다. 개발 모드는 처음부터 열려 있어 이 전환을 타지 않으므로,
// 전환을 직접 일으켜 관문 뒤에 훅을 두는 실수(React #310)를 잡는다.
let openGate: () => void;
vi.mock('../components/BusinessAccess', async original => ({
  ...await original<typeof import('../components/BusinessAccess')>(),
  useBusinessAccess: () => {
    const [ready, setReady] = useState(false);
    openGate = () => setReady(true);
    return { ready, operational: true, enabled: true, user: { isPending: !ready, data: ready ? { data: { roles: ['sales'] } } : undefined } };
  },
}));

afterEach(() => { cleanup(); setApiMode('mock'); });

const P = '/tft/projects/33333333-3333-4333-8333-333333333333';
describe('권한 확인 뒤 화면 열림', () => {
  it.each([
    ['업무 홈', '/tft', 'tft', <RfpHome key="home" />, '업무 홈'],
    ['접수', '/tft/projects', 'tft/projects', <RfpIntake key="intake" />, '접수 및 요구 검토'],
    ['프로젝트', P, 'tft/projects/:projectId', <RfpIntake key="project" />, '접수 및 요구 검토'],
    ['대응표', `${P}/table`, 'tft/projects/:projectId/table', <RfpTable key="table" />, '자사 대응표'],
    ['승인', `${P}/table/approvals`, 'tft/projects/:projectId/table/approvals', <RfpApprovals key="approvals" />, '자사 대응표'],
    ['요구 상세', `${P}/requirements/r1`, 'tft/projects/:projectId/requirements/:requirementId', <RfpRequirement key="requirement" />, '접수 및 요구 검토'],
  ])('%s', async (_name, path, pattern, element, title) => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderAt(path, pattern, element);
    expect(screen.getByRole('heading', { name: title })).toBeTruthy();
    act(() => openGate());
    await screen.findByRole('heading', { name: title });
    expect(error.mock.calls.flat().join(' ')).not.toMatch(/order of Hooks|Rendered more hooks/);
    error.mockRestore();
  });
});
