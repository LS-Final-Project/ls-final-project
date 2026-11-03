import type { DocumentVersion, RequirementSummary } from '../api/rfp';
import type { ContextCategory, ContextEnvelope, ContextGroup } from '../api/rfp-context';
import { equipmentGroups } from './equipment-groups';

export type ResponseReviewGroup = { key: string; name: string; rows: RequirementSummary[] };

/** Only use current assignments whose source identity and requirement version agree. */
export function responseReviewGroups(projectId: string, document: DocumentVersion, rows: RequirementSummary[], context?: ContextEnvelope) {
  const source = new Map((context?.source_refs ?? []).map(ref => [ref.requirement_id, ref]));
  const current = context?.project_id === projectId && context.status === 'CURRENT' && !!context.result
    && rows.every(row => {
      const ref = source.get(row.requirement_id);
      return ref?.version === row.version && ref.document_id === document.document_id && ref.document_version_id === document.document_version_id;
    });
  const categories = new Map<string, ContextCategory[]>();
  if (!current) return { current: false, groups: equipmentGroups(rows), categories };
  const assignments = new Map(context.result!.assignments.map(a => [a.requirement_id, a]));
  const groups: ResponseReviewGroup[] = context.result!.groups.flatMap(group => {
    const members = rows.filter(row => assignments.get(row.requirement_id)?.group_ids.includes(group.entity_id));
    return members.length ? [{ key: group.entity_id, name: contextGroupName(group, context.result!.groups), rows: members }] : [];
  });
  const grouped = new Set(groups.flatMap(group => group.rows.map(row => row.requirement_id)));
  const unknown = rows.filter(row => !grouped.has(row.requirement_id));
  if (unknown.length) groups.push({ key: '__unclassified', name: '설비·과업 연결 미확정', rows: unknown });
  for (const row of rows) {
    const assignment = assignments.get(row.requirement_id);
    categories.set(row.requirement_id, assignment ? [...new Set([assignment.primary_category, ...assignment.additional_categories])] : ['UNCLASSIFIED']);
  }
  return { current: true, groups, categories };
}

function contextGroupName(group: ContextGroup, groups: ContextGroup[]) {
  const names: string[] = [];
  const seen = new Set<string>();
  let current: ContextGroup | undefined = group;
  while (current && !seen.has(current.entity_id)) {
    seen.add(current.entity_id);
    const metadata = [current.tag && `태그 ${current.tag}`, current.site && `위치 ${current.site}`].filter(Boolean);
    names.unshift(`${current.name}${metadata.length ? ` (${metadata.join(' · ')})` : ''}`);
    const parentId: string | null | undefined = current.parent_id;
    current = parentId ? groups.find(candidate => candidate.entity_id === parentId) : undefined;
  }
  return names.join(' › ');
}
