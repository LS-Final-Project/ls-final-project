import { useQuery } from '@tanstack/react-query';
import { getApiMode } from '../api/client';
import { getTicketAudit, type TicketAuditResponse } from '../api/cs';
import { KvGrid, type KvColumn } from '../ls-shell/kv';
import { formatStamp } from '../ls-shell/cs-labels';

type Event = TicketAuditResponse['data']['items'][number];
const ACTION_LABEL: Record<string, string> = { create: '등록', update: '수정' };
const clip = (value: unknown) => { const text = value === null || value === undefined ? '없음' : typeof value === 'object' ? JSON.stringify(value) : String(value); return text.length > 40 ? `${text.slice(0, 40)}…` : text; };
// 감사 이력의 changes는 등록이면 {필드: {to}}, 정정이면 {필드: {from, to}}다. 필드 이름 그대로 한 줄로 줄여 보인다.
function summary(event: Event): string {
  const entries = Object.entries(event.changes as Record<string, { from?: unknown; to?: unknown }>);
  if (entries.length === 0) return '바뀐 내용 없음';
  return entries.map(([field, change]) => 'from' in (change ?? {}) ? `${field} ${clip(change.from)} → ${clip(change.to)}` : `${field} ${clip(change?.to)}`).join(', ');
}

/** 처리 기록. 문의 감사 이력을 판본 순번과 함께 최신이 위로 보인다. */
export default function TicketAudit({ ticketId }: { ticketId: string }) {
  const mode = getApiMode();
  const audit = useQuery({ queryKey: ['ticket-audit', mode, ticketId], queryFn: ({ signal }) => getTicketAudit(ticketId, signal), retry: false, staleTime: 0 });
  const columns: KvColumn<Event>[] = [
    { key: 'at', header: '일시', width: 130, className: 'd', headClass: '', cell: row => formatStamp(row.occurred_at) },
    { key: 'action', header: '구분', width: 44, className: 'c', cell: row => ACTION_LABEL[row.action] ?? row.action },
    { key: 'actor', header: '처리자', width: 110, headClass: '', cell: row => row.actor_user_id ? row.actor_user_id.slice(0, 8) : '시스템' },
    { key: 'changes', header: '바뀐 내용', headClass: '', cell: row => summary(row) },
    { key: 'version', header: '순번', width: 44, className: 'c', cell: row => row.version },
  ];
  if (audit.isPending) return <div className="kv-sub" style={{ margin: 0 }}>처리 기록을 불러오는 중입니다.</div>;
  if (audit.isError) return <div className="kv-sub" style={{ margin: 0 }}>처리 기록을 불러오지 못했습니다. <button className="kb" onClick={() => { void audit.refetch(); }}>다시 시도</button></div>;
  const items = audit.data?.data?.items;
  if (!Array.isArray(items)) return <div className="kv-sub" style={{ margin: 0 }}>처리 기록을 확인할 수 없습니다.</div>;
  const rows = [...items].sort((a, b) => b.version - a.version);
  return <KvGrid label="처리 기록" columns={columns} rows={rows} rowKey={row => row.id} fallback="처리 기록이 없습니다." />;
}
