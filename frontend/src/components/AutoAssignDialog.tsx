import { applyAssignments, previewAssignments, type ApplyRow, type ProposalRow } from '../api/cs-assignments';
import { ASSIGN_REASON_LABEL, familyName, languageName } from '../ls-shell/cs-labels';
import AssignPreviewDialog, { LIMIT, type AssignColumn } from './AssignPreviewDialog';

const COLUMNS: AssignColumn<ProposalRow>[] = [
  { header: '문의', title: item => item.subject ?? undefined, cell: item => item.external_id ?? item.ticket_id.slice(0, 8) },
  { header: '언어', cell: item => languageName(item.body_language) },
  { header: '제품군', title: item => item.product_code, cell: item => item.family ? familyName(item.family) : item.product_code },
  { header: '배정 대상', cell: (item, nameOf) => nameOf(item.user_id) },
  { header: '사유', cell: item => `${ASSIGN_REASON_LABEL[item.reason_code] ?? item.reason_code}${item.user_id && item.candidate_count > 1 ? ` · ${item.candidate_count}명 중 진행 ${item.chosen_open_count ?? 0}건` : ''}` },
];

/** 문의 조회의 자동 배정(KAN-214). 담당자 없는 접수 문의를 규칙으로 미리 보고, 확인하면 그대로 적용한다. */
export default function AutoAssignDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  return <AssignPreviewDialog<ProposalRow, ApplyRow>
    title="자동 배정"
    intro={`담당자 없는 접수 문의(오래된 순 최대 ${LIMIT}건)를 규칙으로 배정합니다. 언어·제품군이 맞는 사람 중 진행 건수가 가장 적은 사람이고, AI가 정하지 않습니다. 담당자 기준표는 가정 자료입니다.`}
    tableLabel="자동 배정 미리보기" emptyText="담당자 없는 접수 문의가 없습니다." columns={COLUMNS}
    keyOf={item => item.ticket_id} resultKeyOf={row => row.ticket_id}
    load={async signal => (await previewAssignments(LIMIT, signal)).data}
    apply={async (preview, signal) => (await applyAssignments({ rule_version: preview.rule_version, items: preview.items.map(item => ({ ticket_id: item.ticket_id, user_id: item.user_id ?? null })) }, signal)).data.items}
    onClose={onClose} onDone={onDone} />;
}
