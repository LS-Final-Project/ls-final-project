import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, getApiMode } from '../api/client';
import { getTicketSummary } from '../api/cs';
import { createTicketSummary } from '../api/cs-workflow';
import { formatStamp } from '../ls-shell/cs-labels';

const FAILURE_LABEL: Record<string, string> = {
  CS_SUMMARY_SCHEMA: '모델 출력 형식이 맞지 않음', CS_SUMMARY_NOT_KOREAN: '한국어가 아님', CS_SUMMARY_UNGROUNDED: '원문에 없는 코드·숫자가 들어감', CS_SUMMARY_COMMITMENT: '승인·확정 약속 문구가 들어감',
};

/**
 * 한국어 요약 한 줄. 저장된 요약을 읽어 보이고, 없으면 담당자가 눌러서 만든다(모델 1회 호출, 비용).
 * 요약은 판정이 아니다(분류·규칙 결과와 별개). 요약 기능이 꺼진 배포(404)는 안내만 한다.
 */
export default function TicketSummary({ ticketId, canWrite }: { ticketId: string; canWrite: boolean }) {
  const mode = getApiMode();
  const summary = useQuery({ queryKey: ['ticket-summary', mode, ticketId], queryFn: ({ signal }) => getTicketSummary(ticketId, signal), retry: false, staleTime: 0 });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  async function create() {
    if (busy) return;
    setBusy(true); setMessage(null);
    try {
      await createTicketSummary(ticketId, new AbortController().signal);
      setMessage({ kind: 'ok', text: '요약을 만들었습니다.' });
    } catch (error) {
      const code = error instanceof ApiError && error.status === 502 ? FAILURE_LABEL[error.code ?? ''] : undefined;
      setMessage({ kind: 'error', text: code ? `요약을 만들지 못했습니다: ${code}.` : error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408 ? error.message
        : '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 최신 요약을 읽었습니다. 반영 여부를 확인하세요.' });
    } finally {
      setBusy(false);
      await summary.refetch();
    }
  }
  const label = <span style={{ marginRight: 8 }}>한국어 요약</span>;
  const row = (children: React.ReactNode) => <div className="kv-sub" style={{ margin: '0 0 6px', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>{label}{children}</div>;
  if (summary.isPending) return row(<span className="unc">불러오는 중</span>);
  if (summary.error instanceof ApiError && summary.error.status === 404) return row(<span className="unc">요약 기능이 이 배포에서 꺼져 있습니다</span>);
  if (summary.isError) return row(<><span className="unc">요약을 불러오지 못했습니다</span><button className="kb" onClick={() => { void summary.refetch(); }}>다시 시도</button></>);
  const record = summary.data.data.summary;
  const attempt = summary.data.data.latest_attempt;
  return <>
    {record
      ? <div className="kv-sub" style={{ margin: '0 0 6px', color: 'var(--erp-text)' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--erp-muted)' }}>{label}<span className="unc">AI 작성 {formatStamp(record.created_at)}, 판정이 아님</span>{record.stale && <span className="unc">원문이 바뀐 뒤의 요약이 아님을 확인하세요(오래됨)</span>}</div>
        <div>{record.summary_ko}</div>
      </div>
      : row(<><span className="unc">아직 만들지 않았습니다</span>{attempt && attempt.outcome !== 'VALID' && <span className="unc">직전 시도 실패{attempt.error_code ? `: ${FAILURE_LABEL[attempt.error_code] ?? attempt.error_code}` : ''}</span>}</>)}
    {canWrite && (!record || record.stale) && <div style={{ margin: '0 0 6px' }}><button className="kb" disabled={busy} title="모델을 한 번 호출합니다(비용). 같은 원문의 성공한 요약이 있으면 그것을 돌려줍니다." onClick={() => void create()}>{busy ? '만드는 중' : record ? '요약 다시 만들기' : '요약 만들기'}</button></div>}
    {message && <div className="kv-sub" role={message.kind === 'error' ? 'alert' : 'status'} style={{ margin: '0 0 6px' }}>{message.text}</div>}
  </>;
}
