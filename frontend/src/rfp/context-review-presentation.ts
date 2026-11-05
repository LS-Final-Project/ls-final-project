import type { DocumentVersion, RequirementSummary } from '../api/rfp';
import type { ContextAssignment, ContextCategory, ContextEnvelope, ContextGroup, ContextSource } from '../api/rfp-context';

export const REVIEW_CATEGORY_LABELS: Record<ContextCategory,string> = { EQUIPMENT:'장비 사양',TASK:'수행 업무',DELIVERABLE:'제출 서류',INTERFACE:'연결·연동',QUALITY:'품질·시험',SCHEDULE:'납기·일정',QUALIFICATION:'참여 자격',COMMERCIAL:'계약·거래 조건',UNCLASSIFIED:'종류 확인 필요' };

export function reviewDisplayText(value: string) {
  return value.replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, '').replace(/\s{2,}/g, ' ').replace(/^[ ·/,-]+|[ ·/,-]+$/g, '').trim();
}

export function reviewRequirementLabel(code: string, index: number, page?: number | null) {
  const fallback = `${page ? `원문 ${page}쪽 · ` : ''}요구 ${index + 1}`;
  if (/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(code)) return fallback;
  const stored = code.match(/^[0-9a-f]{8}:([^:]+):(\d+)$/i);
  if (stored && !stored[1].startsWith('OCR-')) return `조항 ${stored[1]} · 요구 ${stored[2]}`;
  if (/^R-\d+$/i.test(code)) return code;
  if (/^\d+(?:[.-][a-z0-9]+)*$/i.test(code)) return `조항 ${code}`;
  return fallback;
}

export function reviewTargetName(group: ContextGroup, groups: ContextGroup[]) {
  const names: string[] = [], seen = new Set<string>();
  let current: ContextGroup | undefined = group;
  while (current && !seen.has(current.entity_id)) {
    seen.add(current.entity_id);
    const location = [current.tag, current.site].filter((value): value is string => !!value).map(reviewDisplayText).filter(Boolean).join(' · ');
    const name = reviewDisplayText(current.name) || ({SYSTEM:'계통',EQUIPMENT:'설비',TASK:'업무',COMMON:'공통 업무'})[current.kind];
    names.unshift(`${name}${location ? ` · ${location}` : ''}`);
    const parent: string | null | undefined = current.parent_id;
    current = parent ? groups.find(item => item.entity_id === parent) : undefined;
  }
  return names.reverse().join(' / 상위: ');
}

export type ReviewTarget = { key: string; group?: ContextGroup; name: string; rows: RequirementSummary[]; includesChildren: boolean };
export function reviewPresentation(projectId: string, document: DocumentVersion, documents: DocumentVersion[], rows: RequirementSummary[], data?: ContextEnvelope, scope: 'document'|'project' = 'document') {
  const refs = new Map((data?.source_refs ?? []).map(ref => [ref.requirement_id, ref]));
  const current = data?.project_id === projectId && data.status === 'CURRENT' && !!data.result
    && !!data.source_fingerprint && data.source_fingerprint === data.current_source_fingerprint
    && rows.every(row => {
      const ref = refs.get(row.requirement_id);
      return ref?.version === row.version && documents.some(doc => doc.document_id === ref.document_id && doc.document_version_id === ref.document_version_id)
        && (scope === 'project' || ref.document_id === document.document_id && ref.document_version_id === document.document_version_id);
    });
  const assignments = new Map<string, ContextAssignment>(current ? data.result!.assignments.map(a => [a.requirement_id, a]) : []);
  const groups = current ? data.result!.groups : [];
  const targets: ReviewTarget[] = groups.map(group => {
    const descendants = new Set([group.entity_id]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const child of groups) if (child.parent_id && descendants.has(child.parent_id) && !descendants.has(child.entity_id)) { descendants.add(child.entity_id); changed = true; }
    }
    return { key: JSON.stringify([data!.version, document.document_version_id, scope, group.entity_id]), group, name: reviewTargetName(group, groups), rows: rows.filter(row => assignments.get(row.requirement_id)?.group_ids.some(id => descendants.has(id))), includesChildren: descendants.size > 1 };
  }).filter(target => target.rows.length > 0);
  const assigned = new Set(targets.flatMap(target => target.rows.map(row => row.requirement_id)));
  const unorganized = rows.filter(row => !assigned.has(row.requirement_id));
  if (unorganized.length) targets.push({ key: JSON.stringify(['unorganized', document.document_version_id, scope]), name: '미정리 요구', rows: unorganized, includesChildren: false });
  return { current, targets, assignments, refs: current ? refs : new Map<string, ContextSource>(), uniqueCount: new Set(rows.map(row => row.requirement_id)).size, membershipCount: rows.reduce((total, row) => total + new Set(assignments.get(row.requirement_id)?.group_ids.filter(id => groups.some(group => group.entity_id === id)) ?? []).size, 0) };
}
