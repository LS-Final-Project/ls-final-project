import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError } from '../api/client';
import { listUserNames } from '../api/platform';
import { KvModal } from '../ls-shell/kv';

export const LIMIT = 50;
// 문의(ticket)와 개선요청(request) 적용 결과 코드. 개선요청은 NOT_RECEIVED 대신 NOT_OPEN이다.
export const OUTCOME_LABEL: Record<string, string> = {
  ASSIGNED: '배정', ALREADY_ASSIGNED: '이미 담당 있음', NOT_RECEIVED: '접수 상태 아님', NOT_OPEN: '진행 중 아님', NOT_FOUND: '문의 없음',
  PREVIEW_CHANGED: '미리보기와 달라짐', NO_MATCH: '맞는 담당자 없음',
};

type Proposal = { user_id?: string | null };
type Outcome = { outcome: string };
export type AssignColumn<T> = { header: string; cell: (item: T, nameOf: (id: string | null | undefined) => string) => ReactNode; title?: (item: T) => string | undefined };

/**
 * 규칙 자동 배정의 미리보기→적용 창(KAN-214, C/S 관리자만). 문의 조회와 개선요청 목록이 같이 쓴다.
 * 미리보기는 아무것도 쓰지 않는다. 적용은 서버가 같은 규칙으로 다시 정해 미리보기와 같은 것만 쓴다(멱등).
 */
export default function AssignPreviewDialog<T extends Proposal, R extends Outcome>({ title, intro, tableLabel, emptyText, columns, keyOf, load, apply: send, resultKeyOf, outcomeLabels, onClose, onDone }: {
  title: string; intro: string; tableLabel: string; emptyText: string;
  columns: AssignColumn<T>[];
  keyOf: (item: T) => string;
  load: (signal: AbortSignal) => Promise<{ rule_version: string; items: T[] }>;
  apply: (preview: { rule_version: string; items: T[] }, signal: AbortSignal) => Promise<R[]>;
  resultKeyOf: (row: R) => string;
  outcomeLabels?: Record<string, string>;
  onClose: () => void; onDone: () => void;
}) {
  const [preview, setPreview] = useState<{ rule_version: string; items: T[] } | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [result, setResult] = useState<R[] | null>(null);
  const alive = useRef(true);
  const loader = useRef(load);

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    (async () => {
      try {
        const data = await loader.current(controller.signal);
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
      const rows = await send(preview, AbortSignal.timeout(30_000));
      if (!alive.current) return;
      setResult(rows);
      onDone();
    } catch (caught) {
      if (alive.current) setError(caught instanceof ApiError && caught.code === 'CS_ASSIGNMENT_RULES_CHANGED' ? '배정 규칙이 바뀌었습니다. 닫고 다시 미리보세요.' : caught instanceof ApiError ? caught.message : '적용 결과를 알 수 없습니다. 목록에서 담당을 확인하세요.');
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  const byKey = new Map((result ?? []).map(row => [resultKeyOf(row), row]));
  const assigned = result?.filter(row => row.outcome === 'ASSIGNED').length ?? 0;
  const label = (outcome: string) => outcomeLabels?.[outcome] ?? OUTCOME_LABEL[outcome] ?? outcome;
  const reasons = (result ?? []).filter(row => row.outcome !== 'ASSIGNED').reduce<Record<string, number>>((acc, row) => ({ ...acc, [label(row.outcome)]: (acc[label(row.outcome)] ?? 0) + 1 }), {});
  const span = columns.length + (result ? 1 : 0);

  return <KvModal title={title} onClose={() => !busy && onClose()}>
    <div className="kv-sub" style={{ margin: '0 0 8px' }}>{intro}</div>
    {busy && !preview && <div className="kv-sub" role="status">미리보기를 만드는 중입니다</div>}
    {error && <div className="kv-err" role="alert" style={{ padding: '6px 10px', margin: '0 0 8px' }}>{error}</div>}
    {preview && <>
      {result
        ? <div className="kv-sub" role="status" style={{ margin: '0 0 6px' }}>끝남: 배정 {assigned}건, 미배정 {result.length - assigned}건{Object.keys(reasons).length > 0 && ` (${Object.entries(reasons).map(([text, count]) => `${text} ${count}`).join(', ')})`}</div>
        : <div className="kv-sub" style={{ margin: '0 0 6px' }}>미리보기 {preview.items.length}건, 배정 대상 {assignable.length}건, 맞는 담당자 없음 {preview.items.length - assignable.length}건</div>}
      <div style={{ maxHeight: '45vh', overflow: 'auto' }}>
        <table className="kv-grid free" aria-label={tableLabel}>
          <thead><tr>{columns.map(column => <th key={column.header}>{column.header}</th>)}{result && <th>결과</th>}</tr></thead>
          <tbody>{preview.items.length === 0 ? <tr><td colSpan={span}>{emptyText}</td></tr>
            : preview.items.map(item => {
              const done = byKey.get(keyOf(item));
              return <tr key={keyOf(item)}>
                {columns.map(column => <td key={column.header} title={column.title?.(item)}>{column.cell(item, nameOf)}</td>)}
                {result && <td>{done ? label(done.outcome) : '-'}</td>}
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
