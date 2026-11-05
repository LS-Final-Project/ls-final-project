import { afterEach, expect, it, vi } from 'vitest';
import { listRequirements, type RequirementSummary } from '../api/rfp';
import type { PartnerCandidate } from '../api/rfp-supplement';
import { equipmentGroups, groupCounts, groupPartners, readDocumentRequirements } from './equipment-groups';
import { MOCK_REQUIREMENTS } from '../mocks/rfp-table';
vi.mock('../api/rfp', async original => ({ ...await original<object>(), listRequirements: vi.fn() }));
afterEach(() => vi.clearAllMocks());
const row = (id: string, equipment: string | null, code: string, extra: Partial<RequirementSummary> = {}): RequirementSummary => ({ ...MOCK_REQUIREMENTS[0], provenance_kind: 'ai_draft', requirement_id: id, equipment, code, ...extra });

it('장비 표기·원문 절을 구분하고 과업/미분류·OCR 위치를 별도로 유지한다', () => {
  const rows = [row('a', '154kV GIS', '1234abcd:1.3.1:1'), row('b', '154kV GIS', '1234abcd:1.3.2:1'), row('c', '154kV GIS', '1234abcd:2.1.1:1'), row('d', '170kV GIS', '1234abcd:1.3.1:2'), row('e', null, '1234abcd:1.3.1:3', { task: '현장 시험' }), row('f', null, '1234abcd:1.3.1:4'), row('g', '154kV GIS', '1234abcd:OCR-PDF-P0001.1:1'), row('h', '154kV GIS', '1234abcd:OCR-PDF-P0002.1:1')];
  const groups = equipmentGroups(rows);
  expect(groups).toHaveLength(7);
  expect(groups[0].rows.map(r => r.requirement_id)).toEqual(['a', 'b']);
  expect(groups[1].section).toBe('2.1');
  expect(groups[3].kind).toBe('task');
  expect(groups[4].kind).toBe('unclassified');
  expect(groups[5].section).toBe('OCR-PDF-P0001.1');
  expect(groups.flatMap(g => g.rows)).toHaveLength(rows.length);
});

it('사양 차이와 근거 부족을 합쳐 대응불가로 만들지 않고 검토·질문을 별도로 센다', () => {
  const [group] = equipmentGroups([row('a','변압기','R-01'), row('b','변압기','R-02', { status:'INSUFFICIENT_EVIDENCE', needs_review:true, needs_rereview:true, open_questions:2, rnd_check:'PENDING' }), row('c','변압기','R-03',{status:'EVIDENCE_FOUND'})]);
  expect(groupCounts(group)).toEqual({ evidence:1, gap:1, conditional:0, unknown:1, review:1, questions:2, rnd:1 });
  expect(group.section).toBeNull();
});

it('협력사 집계는 실제 요구/프로필 판본이 일치한 저장등급만 사용한다', () => {
  const [group] = equipmentGroups([row('a','변압기','R-01',{version:2})]);
  const candidate = { candidate_code:'P', profile_version:3, grades:[{requirement_id:'a',requirement_version:2,profile_version:3,grade:'REVIEW'},{requirement_id:'a',requirement_version:1,profile_version:3,grade:'MANDATORY_CONFIRMED'},{requirement_id:'b',requirement_version:2,profile_version:3,grade:'MANDATORY_CONFIRMED'},{requirement_id:'a',requirement_version:2,profile_version:2,grade:'MANDATORY_CONFIRMED'}] } as PartnerCandidate;
  expect(groupPartners(group,[candidate])[0].grades).toHaveLength(1);
  expect(groupPartners(group,[{...candidate,grades:[{requirement_id:'a',grade:'MANDATORY_CONFIRMED'}] as never}])).toEqual([]);
});

it('모든 커서를 순차 조회하고 AbortSignal을 같은 요청에 전달한다', async () => {
  vi.mocked(listRequirements).mockResolvedValueOnce({data:{items:[row('a','장비','R-01')],next_cursor:'second'}}).mockResolvedValueOnce({data:{items:[row('b','장비','R-02')],next_cursor:null}});
  const signal = new AbortController().signal;
  const rows = await readDocumentRequirements('p',{document_version_id:'v'},signal);
  expect(rows.map(r=>r.requirement_id)).toEqual(['a','b']);
  expect(listRequirements).toHaveBeenNthCalledWith(2,'p',{document_version_id:'v'},100,'second',{signal});
});

it.each(['repeated-cursor','duplicate-row','page-error'])('불완전 목록은 집계하지 않는다: %s', async failure => {
  vi.mocked(listRequirements).mockResolvedValueOnce({data:{items:[row('a','장비','R-01')],next_cursor:'second'}});
  if(failure==='page-error') vi.mocked(listRequirements).mockRejectedValueOnce(new Error('403'));
  else vi.mocked(listRequirements).mockResolvedValueOnce({data:{items:[row(failure==='duplicate-row'?'a':'b','장비','R-02')],next_cursor:failure==='repeated-cursor'?'second':null}});
  await expect(readDocumentRequirements('p',{},new AbortController().signal)).rejects.toThrow();
  expect(listRequirements).toHaveBeenCalledTimes(2);
});

it('문서를 떠나 취소되면 다음 페이지를 호출하지 않는다', async () => {
  const controller = new AbortController();
  vi.mocked(listRequirements).mockImplementationOnce(async () => { controller.abort(); return {data:{items:[],next_cursor:'next'}}; });
  await expect(readDocumentRequirements('p',{},controller.signal)).rejects.toThrow();
  expect(listRequirements).toHaveBeenCalledTimes(1);
});
