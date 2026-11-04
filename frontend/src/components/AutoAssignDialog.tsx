import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { applyAssignments, previewAssignments, type ApplyRow, type PreviewResponse, type ProposalRow } from '../api/cs-assignments';
import { listUserNames } from '../api/platform';
import { KvModal } from '../ls-shell/kv';
import { ASSIGN_REASON_LABEL, familyName, languageName } from '../ls-shell/cs-labels';

const LIMIT = 50;
const OUTCOME_LABEL: Record<ApplyRow['outcome'], string> = {
  ASSIGNED: '배정', ALREADY_ASSIGNED: '이미 담당 있음', NOT_RECEIVED: '접수 상태 아님', NOT_FOUND: '문의 없음',
  PREVIEW_CHANGED: '미리보기와 달라짐', NO_MATCH: '맞는 담당자 없음',
};

/**
 * 문의 조회의 자동 배정(KAN-214, C/S 관리자만). 담당 없는 접수 문의를 규칙으로 미리 보고, 확인하면 그대로 적용한다.
 * 미리보기는 아무것도 쓰지 않는다. 적용은 서버가 같은 규칙으로 다시 정해 미리보기와 같은 것만 쓴다(멱등).
 */
export default function AutoAssignDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [preview, setPreview] = useState<PreviewResponse['data'] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [result, setResult] = useState<ApplyRow[] | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    (async () => {
      try {
        const data = (await previewAssignments(LIMIT, controller.signal)).data;
        const ids = [...new Set(data.items.map(item => item.user_id).filter((id): id is string => !!id))];
        // 이름은 플랫폼만 안다. 못 읽어도 미리보기는 ID로 보인다.
        const found = ids.length ? await listUserNames(ids, controller.signal).then(r => r.data.items, () => []) : [];
        if (!alive.current) return;
        setNames(Object.fromEntries(found.map(item => [item.user_id, item.display_name])));
        setPreview(data);
      } catch (caught) {
        if (alive.current && !controller.signal.aborted) setError(caught instanceof ApiError ? caught.message : '미리보기를 불러오지 못했습니다.');
      } finally {
        if (alive.current) setBusy(false);
      }
    })();
    return () => { alive.current = false; controller.abort(); };
  }, []);

  const nameOf = (id: string | null | undefined) => !id ? '-' : names[id] ?? `사용자 ${id.slice(0, 8)}`;
  const assignable = preview?.items.filter(item => item.user_id) ?? [];

  async function apply() {
    if (!preview || busy) return;
    setBusy(true); setError('');
    try {
      const body = { rule_version: preview.rule_version, items: preview.items.map(item => ({ ticket_id: item.ticket_id, user_id: item.user_id ?? null })) };
      const response = await applyAssignments(body, AbortSignal.timeout(30_000));
      if (!alive.current) return;
      setResult(response.data.items);
      onDone();
    } catch (caught) {
      if (alive.current) setError(caught instanceof ApiError && caught.code === 'CS_ASSIGNMENT_RULES_CHANGED' ? '배정 규칙이 바뀌었습니다. 닫고 다시 미리보세요.' : caught instanceof ApiError ? caught.message : '적용 결과를 알 수 없습니다. 목록에서 담당을 확인하세요.');
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  const byTicket = new Map((result ?? []).map(row => [row.ticket_id, row]));
  const assigned = result?.filter(row => row.outcome === 'ASSIGNED').length ?? 0;
  const reasons = (result ?? []).filter(row => row.outcome !== 'ASSIGNED').reduce<Record<string, number>>((acc, row) => ({ ...acc, [OUTCOME_LABEL[row.outcome]]: (acc[OUTCOME_LABEL[row.outcome]] ?? 0) + 1 }), {});
  const number = (item: ProposalRow) => item.external_id ?? item.ticket_id.slice(0, 8);

  return <KvModal title="자동 배정" onClose={() => !busy && onClose()}>
    <div className="kv-sub" style={{ margin: '0 0 8px' }}>담당자 없는 접수 문의(오래된 순 최대 {LIMIT}건)를 규칙으로 배정합니다. 언어·제품군이 맞는 사람 중 진행 건수가 가장 적은 사람이고, AI가 정하지 않습니다. 담당자 기준표는 가정 자료입니다.</div>
    {busy && !preview && <div className="kv-sub" role="status">미리보기를 만드는 중입니다</div>}
    {error && <div className="kv-err" role="alert" style={{ padding: '6px 10px', margin: '0 0 8px' }}>{error}</div>}
    {preview && <>
      {result
        ? <div className="kv-sub" role="status" style={{ margin: '0 0 6px' }}>끝남: 배정 {assigned}건, 미배정 {result.length - assigned}건{Object.keys(reasons).length > 0 && ` (${Object.entries(reasons).map(([label, count]) => `${label} ${count}`).join(', ')})`}</div>
        : <div className="kv-sub" style={{ margin: '0 0 6px' }}>미리보기 {preview.items.length}건, 배정 대상 {assignable.length}건, 맞는 담당자 없음 {preview.items.length - assignable.length}건</div>}
      <div style={{ maxHeight: '45vh', overflow: 'auto' }}>
        <table className="kv-grid free" aria-label="자동 배정 미리보기">
          <thead><tr><th>문의</th><th>언어</th><th>제품군</th><th>배정 대상</th><th>사유</th>{result && <th>결과</th>}</tr></thead>
          <tbody>{preview.items.length === 0 ? <tr><td colSpan={result ? 6 : 5}>담당자 없는 접수 문의가 없습니다.</td></tr>
            : preview.items.map(item => {
              const family = item.family;
              const done = byTicket.get(item.ticket_id);
              return <tr key={item.ticket_id}>
                <td title={item.subject ?? undefined}>{number(item)}</td>
                <td>{languageName(item.body_language)}</td>
                <td title={item.product_code}>{family ? familyName(family) : item.product_code}</td>
                <td>{nameOf(item.user_id)}</td>
                <td>{ASSIGN_REASON_LABEL[item.reason_code] ?? item.reason_code}{item.user_id && item.candidate_count > 1 ? ` · ${item.candidate_count}명 중 진행 ${item.chosen_open_count ?? 0}건` : ''}</td>
                {result && <td>{done ? OUTCOME_LABEL[done.outcome] : '-'}</td>}
              </tr>;
            })}</tbody>
        </table>
      </div>
    </>}
    <div className="kv-actions">
      <button type="button" className="kb" disabled={busy && !!preview} onClick={onClose}>{result ? '닫기' : '취소'}</button>
      {!result && <button type="button" className="kb primary" disabled={busy || !preview || assignable.length === 0} onClick={() => void apply()}>배정 적용</button>}
    </div>
  </KvModal>;
}
