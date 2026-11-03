import { listRequirements, type RequirementFilters, type RequirementSummary } from '../api/rfp';
import type { PartnerCandidate } from '../api/rfp-supplement';

// Summary pages only: do not fetch 1,000 full requirement/comparison documents to draw the overview.
export async function readDocumentRequirements(projectId: string, filters: RequirementFilters, signal: AbortSignal, options: { useMockFallback?: boolean } = {}): Promise<RequirementSummary[]> {
  const rows: RequirementSummary[] = [];
  const cursors = new Set<string>();
  const ids = new Set<string>();
  let cursor: string | undefined;
  do {
    signal.throwIfAborted();
    const response = await listRequirements(projectId, filters, 100, cursor, { signal, ...options });
    signal.throwIfAborted();
    for (const row of response.data.items) {
      if (ids.has(row.requirement_id)) throw new Error('요구 목록이 조회 중 변경되었습니다. 다시 조회해 주세요.');
      ids.add(row.requirement_id);
      rows.push(row);
    }
    cursor = response.data.next_cursor ?? undefined;
    if (cursor && (cursors.has(cursor) || cursors.size >= 199)) throw new Error('요구 목록을 끝까지 확인하지 못했습니다. 다시 조회해 주세요.');
    if (cursor) cursors.add(cursor);
  } while (cursor);
  return rows;
}

export type EquipmentGroup = {
  key: string;
  name: string;
  section: string | null;
  kind: 'equipment' | 'task' | 'unclassified';
  rows: RequirementSummary[];
};

function sectionOf(code: string): string | null {
  // Stored code is document-prefix:clause-number:ordinal. Legacy display codes are not clause evidence.
  const parts = code.split(':');
  if (parts.length !== 3 || !/^[0-9a-f]{8}$/i.test(parts[0])) return null;
  const clause = parts[1];
  const dotted = clause.match(/^(\d+\.\d+)(?:\.|$)/);
  return dotted?.[1] ?? clause;
}

export function equipmentGroups(rows: RequirementSummary[]): EquipmentGroup[] {
  const groups = new Map<string, EquipmentGroup>();
  for (const row of rows) {
    // Exact equipment wording only. No alias/voltage/location guessing or cross-document aggregation.
    const equipment = row.equipment?.trim();
    const task = row.task?.trim();
    const kind = equipment ? 'equipment' : task ? 'task' : 'unclassified';
    const name = equipment || (task ? `공통 과업 · ${task}` : '공통·문서 / 장비 미분류');
    const section = sectionOf(row.code);
    const key = JSON.stringify([kind, name, section]);
    const group = groups.get(key) ?? { key, kind, name, section, rows: [] };
    group.rows.push(row);
    groups.set(key, group);
  }
  return [...groups.values()];
}

export function groupCounts(group: EquipmentGroup) {
  const count = (status: RequirementSummary['status']) => group.rows.filter(r => r.status === status).length;
  return {
    evidence: count('EVIDENCE_FOUND'), gap: count('SPEC_GAP'), conditional: count('CONDITIONAL'), unknown: count('INSUFFICIENT_EVIDENCE'),
    review: group.rows.filter(r => r.needs_review || r.needs_rereview).length,
    questions: group.rows.reduce((n, r) => n + r.open_questions, 0),
    rnd: group.rows.filter(r => r.rnd_check === 'PENDING').length,
  };
}

export function groupPartners(group: EquipmentGroup, candidates: PartnerCandidate[]) {
  const versions = new Map(group.rows.map(r => [r.requirement_id, r.version]));
  return candidates.flatMap(candidate => {
    const grades = candidate.grades.filter(g => versions.has(g.requirement_id)
      && g.requirement_version === versions.get(g.requirement_id) && g.profile_version === candidate.profile_version);
    return grades.length ? [{ candidate, grades }] : [];
  });
}
