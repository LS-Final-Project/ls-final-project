import { describe, expect, it } from 'vitest';
import type { DocumentVersion, RequirementSummary } from '../api/rfp';
import type { ContextEnvelope } from '../api/rfp-context';
import { responseReviewGroups } from './response-review-groups';
const document = { document_id: 'doc', document_version_id: 'dv' } as DocumentVersion;
const rows = [{ requirement_id: 'r1', version: 2, equipment: '변압기', code: '12345678:1.1:1' }, { requirement_id: 'r2', version: 1, equipment: '변압기', code: '12345678:2.1:1' }] as RequirementSummary[];
const context = () => ({ project_id: 'p', status: 'CURRENT', source_refs: rows.map(r => ({ requirement_id: r.requirement_id, version: r.version, document_id: 'doc', document_version_id: 'dv' })), result: { groups: [{ entity_id: 'g', name: '실제 맥락 설비' }], assignments: [{ requirement_id: 'r1', group_ids: ['g'], primary_category: 'EQUIPMENT', additional_categories: ['QUALITY'] }] } }) as ContextEnvelope;
describe('responseReviewGroups', () => {
  it('uses actual categories and retains unassigned requirements', () => {
    const result = responseReviewGroups('p', document, rows, context());
    expect(result.current).toBe(true);
    expect(result.groups.map(g => g.rows.map(r => r.requirement_id))).toEqual([['r1'], ['r2']]);
    expect(result.categories.get('r1')).toEqual(['EQUIPMENT', 'QUALITY']);
    expect(result.categories.get('r2')).toEqual(['UNCLASSIFIED']);
  });
  it.each(['STALE', 'NOT_ANALYZED'] as const)('falls back to exact document equipment sections for %s', status => {
    const data = context(); data.status = status;
    const result = responseReviewGroups('p', document, rows, data);
    expect(result.current).toBe(false);
    expect(result.groups).toHaveLength(2);
    expect(result.categories.size).toBe(0);
  });
  it('rejects requirement-version, document-version, document-identity and project mismatches', () => {
    for (const mismatch of ['version', 'document_version_id', 'document_id', 'project']) {
      const data = context();
      if (mismatch === 'project') data.project_id = 'other';
      else Object.assign(data.source_refs[0], { [mismatch]: mismatch === 'version' ? 1 : 'other' });
      expect(responseReviewGroups('p', document, rows, data).current).toBe(false);
    }
  });
});

it('keeps identical equipment names separate using only recorded hierarchy, tags and locations', () => {
  const data = context();
  data.result!.groups = [
    { entity_id: 'system', name: '수전 계통', kind: 'SYSTEM', evidence_requirement_ids: [], diagram_source_ids: [], needs_review: false },
    { entity_id: 'a', name: '변압기', tag: 'TR-A', site: '동관', parent_id: 'system', kind: 'EQUIPMENT', evidence_requirement_ids: [], diagram_source_ids: [], needs_review: false },
    { entity_id: 'b', name: '변압기', tag: 'TR-B', site: '서관', parent_id: 'system', kind: 'EQUIPMENT', evidence_requirement_ids: [], diagram_source_ids: [], needs_review: false },
  ];
  data.result!.assignments[0].group_ids = ['a'];
  data.result!.assignments.push({ ...data.result!.assignments[0], requirement_id: 'r2', group_ids: ['b'] });
  const result = responseReviewGroups('p', document, rows, data);
  expect(result.groups.map(g => g.key)).toEqual(['a', 'b']);
  expect(result.groups.map(g => g.name)).toEqual(['수전 계통 › 변압기 (태그 TR-A · 위치 동관)', '수전 계통 › 변압기 (태그 TR-B · 위치 서관)']);
  expect(result.groups.map(g => g.rows.map(r => r.requirement_id))).toEqual([['r1'], ['r2']]);
});
