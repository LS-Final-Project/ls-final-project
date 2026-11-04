import { applyRequestAssignments, previewRequestAssignments, type RequestApplyRow, type RequestProposalRow } from '../api/cs-assignments';
import { familyName } from '../ls-shell/cs-labels';
import AssignPreviewDialog, { LIMIT, type AssignColumn } from './AssignPreviewDialog';

const REASON: Record<string, string> = { FAMILY: '제품군 담당', NO_MATCH: '맞는 담당자 없음' };
const COLUMNS: AssignColumn<RequestProposalRow>[] = [
  { header: '요청(제품·고장)', title: item => item.family ? familyName(item.family) : undefined, cell: item => `${item.product_code} · ${item.fault_id ?? '고장 미확인'}` },
  { header: '배정 대상', cell: (item, nameOf) => nameOf(item.user_id) },
  { header: '사유', cell: item => `${REASON[item.reason_code] ?? item.reason_code}${item.user_id && item.candidate_count > 1 ? ` · ${item.candidate_count}명 중 진행 ${item.chosen_open_count ?? 0}건` : ''}` },
];

/**
 * 개선요청 목록의 미배정 자동 배정(KAN-214). 자동 배정 전에 만들어져 C/S 담당(cs_owner)이 빈 진행 요청을
 * 제품군 규칙으로 미리 보고 적용한다. 언어는 보지 않는다(contracts/cs/assignment.md 개선요청 담당).
 */
export default function ImprovementAssignDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  return <AssignPreviewDialog<RequestProposalRow, RequestApplyRow>
    title="미배정 자동 배정"
    intro={`C/S 담당이 없는 진행 중 개선요청(오래된 순 최대 ${LIMIT}건)을 규칙으로 배정합니다. 요청 제품군을 맡는 사람 중 진행 개선요청이 가장 적은 사람이고, AI가 정하지 않습니다. 담당자 기준표는 가정 자료입니다.`}
    tableLabel="미배정 자동 배정 미리보기" emptyText="C/S 담당이 없는 진행 중 개선요청이 없습니다." columns={COLUMNS}
    keyOf={item => item.request_id} resultKeyOf={row => row.request_id} outcomeLabels={{ NOT_FOUND: '요청 없음' }}
    load={async signal => (await previewRequestAssignments(LIMIT, signal)).data}
    apply={async (preview, signal) => (await applyRequestAssignments({ rule_version: preview.rule_version, items: preview.items.map(item => ({ request_id: item.request_id, user_id: item.user_id ?? null })) }, signal)).data.items}
    onClose={onClose} onDone={onDone} />;
}
