// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { RequirementSummary } from '../api/rfp';
import type { ProjectDetail } from '../api/tft';
import { MOCK_WORKSPACE_REQUIREMENTS } from '../mocks/rfp-workspace';
import { renderAt } from '../test/render';
import { Decide, eqKey, makeEqKey } from './RfpWorkspace';

vi.mock('./OriginalDocument', () => ({ OriginalDocument: () => null }));
afterEach(() => { cleanup(); });

const project = { id: 'p1', name: 'RFP-91', sales_user_id: 'sales', responsible_user_id: 'boss' } as unknown as ProjectDetail;
// 편차 행 하나(임피던스)를 대표로, 같은 요구 하나를 구성원으로 만든다(KAN-223 중복). 맥락 뒤 바뀐 요구 하나는 stale.
const rep = MOCK_WORKSPACE_REQUIREMENTS.find(r => r.attribute === '임피던스' && r.bid_verdict === 'DEVIATION') as RequirementSummary;
const member: RequirementSummary = { ...rep, requirement_id: '99999999-0000-4000-8000-000000000001', code: 'cb11aed0:OCR-DOCX-0004.1:1',
  duplicate: { representative_id: rep.requirement_id, member_count: 2, is_representative: false },
  decision: { ...rep.decision!, needed: false, follows: rep.requirement_id } };
const rows = MOCK_WORKSPACE_REQUIREMENTS.map(r => r.requirement_id === rep.requirement_id
  ? { ...r, duplicate: { representative_id: rep.requirement_id, member_count: 2, is_representative: true } }
  : r.attribute === '단시간 내전류' ? { ...r, context_stale: true } : r).concat(member);
const show = () => renderAt('/tft/projects/p1', 'tft/projects/:projectId', <Decide project={project} documents={[]} rows={rows} role="sales" actorId="sales" />);

it('설비 묶음은 맥락 태그가 있으면 태그, 없으면 설비 계열 이름이다', () => {
  expect(eqKey({ group: { group_id: 'g', tag: 'MV-SWG-01', name: '중전압 개폐반' }, bucket: 'MV', equipment: '개폐반' })).toBe('MV-SWG-01');
  expect(eqKey({ group: null, bucket: 'COMMON', equipment: '수전점' })).toBe('공통');
  expect(eqKey({ group: null, bucket: 'OTHER', equipment: '버스덕트' })).toBe('기타');
});

it('설비별 남은 건수는 계열 몇 묶음, 중복 구성원은 대표 아래로 접고 대표만 결정한다, 맥락이 바뀌면 다시 정리하라고 한다', async () => {
  show();
  expect((await screen.findByText(/^MV \d+, TR \d+, LV \d+, 공통 \d+$/)).textContent).toMatch(/^MV/);
  expect(screen.getByText(/맥락 정리를 다시 하세요/)).toBeTruthy();
  expect(screen.queryByText('대표를 따름')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '원문 2곳' }));
  expect(screen.getByText('대표를 따름')).toBeTruthy();
  fireEvent.click(screen.getAllByRole('button', { name: '임피던스' }).at(-1)!);
  expect(await screen.findByText(/대표 요구에서 결정합니다/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '대표로 이동' }));
  expect(screen.queryByText(/대표 요구에서 결정합니다/)).toBeNull();
});

it('태그 없는 요구는 같은 계열의 태그가 하나뿐이면 그 태그로 묶는다', () => {
  const rows = [
    { group: { group_id: 'g', tag: 'MV-SWG-01', name: '중전압 개폐반' }, bucket: 'MV' as const, equipment: null },
    { group: null, bucket: 'MV' as const, equipment: '개폐반' },
    { group: null, bucket: 'COMMON' as const, equipment: null },
  ];
  const key = makeEqKey(rows);
  expect(rows.map(key)).toEqual(['MV-SWG-01', 'MV-SWG-01', '공통']);
});
