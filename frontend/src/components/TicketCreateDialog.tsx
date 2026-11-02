import { useRef, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { createTicket, type TicketCreateBody } from '../api/cs-workflow';
import { KvModal } from '../ls-shell/kv';
import { CHANNEL_LABEL, type TicketChannel } from '../ls-shell/cs-labels';

const CUSTOMER = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const text = (form: FormData, name: string) => String(form.get(name) ?? '').trim();

/**
 * 문의 직접 등록(CS_K_List 신규 등록). 전화·채팅처럼 메일로 들어오지 않은 문의를 담당자가 넣는다.
 * 서버가 출처·판본·ID를 정하고 새 문의는 접수 상태다. 등록하면 그 문의의 처리 화면으로 간다.
 * 응답을 알 수 없으면(408·5xx) 같은 요청을 다시 보내지 않고 목록에서 확인하게 한다(중복 문의 방지).
 */
export default function TicketCreateDialog({ products, onClose, onCreated }: { products: string[]; onClose: () => void; onCreated: () => void }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [unknown, setUnknown] = useState(false);
  const running = useRef(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (running.current || unknown) return;
    const form = new FormData(event.currentTarget);
    const customer = text(form, 'customer');
    if (!CUSTOMER.test(customer)) { setError('고객 ID는 영문·숫자와 . _ - 만 쓸 수 있고 64자 이하입니다.'); return; }
    const body: TicketCreateBody = {
      channel: text(form, 'channel') as TicketChannel, body_language: text(form, 'language'), customer_external_id: customer,
      product_code: text(form, 'product'), body: text(form, 'body'),
      subject: text(form, 'subject') || null, fw_version_reported: text(form, 'fw') || null, error_code_reported: text(form, 'error') || null,
    };
    running.current = true; setBusy(true); setError('');
    try {
      const created = await createTicket(body, new AbortController().signal);
      onCreated();
      onClose();
      navigate(`/cs/tickets/${encodeURIComponent(created.data.id)}`);
    } catch (caught) {
      const status = caught instanceof ApiError ? caught.status : 0;
      if (status >= 400 && status < 500 && status !== 408) setError((caught as ApiError).message);
      else { setError('등록 결과를 확인하지 못했습니다. 같은 문의를 다시 등록하지 말고 목록을 조회해 먼저 확인하세요.'); setUnknown(true); onCreated(); }
    } finally { running.current = false; setBusy(false); }
  }

  return <KvModal title="문의 신규 등록" onClose={() => !busy && onClose()}>
    <form onSubmit={event => void submit(event)}>
      <div className="kv-sub" style={{ margin: '0 0 8px' }}>메일로 들어오지 않은 문의를 직접 등록합니다. 등록하면 접수 상태로 시작하고 처리 화면으로 이동합니다.</div>
      <div className="kv-field"><span className="req">접수 경로</span><select name="channel" aria-label="접수 경로" defaultValue="PHONE" disabled={busy}>{(Object.keys(CHANNEL_LABEL) as TicketChannel[]).map(value => <option key={value} value={value}>{CHANNEL_LABEL[value]}</option>)}</select></div>
      <div className="kv-field"><span className="req">고객 ID</span><input name="customer" aria-label="고객 ID" required maxLength={64} disabled={busy} /></div>
      <div className="kv-field"><span className="req">제품 코드</span><input name="product" aria-label="제품 코드" list="ticket-create-products" required maxLength={64} disabled={busy} /><datalist id="ticket-create-products">{products.map(code => <option key={code} value={code} />)}</datalist></div>
      <div className="kv-field"><span className="req">본문 언어</span><input name="language" aria-label="본문 언어" defaultValue="ko" required minLength={2} maxLength={16} disabled={busy} /></div>
      <div className="kv-field"><span>제목</span><input name="subject" aria-label="제목" maxLength={500} disabled={busy} /></div>
      <div className="kv-field"><span className="req">본문</span><textarea name="body" aria-label="본문" rows={6} required maxLength={50000} disabled={busy} /></div>
      <div className="kv-field"><span>보고된 펌웨어</span><input name="fw" aria-label="보고된 펌웨어" maxLength={64} disabled={busy} /></div>
      <div className="kv-field"><span>보고된 에러코드</span><input name="error" aria-label="보고된 에러코드" maxLength={64} disabled={busy} /></div>
      {error && <div className="kv-err" role="alert" style={{ margin: '6px 0', padding: '6px 10px' }}>{error}</div>}
      <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={onClose}>{unknown ? '닫기' : '취소'}</button><button type="submit" className="kb primary" disabled={busy || unknown}>{busy ? '등록 중' : '등록'}</button></div>
    </form>
  </KvModal>;
}
