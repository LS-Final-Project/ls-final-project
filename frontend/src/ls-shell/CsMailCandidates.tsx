import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getMailContent, listCsCandidates, reclassifyMail, registerTicket, type CsCandidate } from '../api/mail';
import { KvGrid, KvModal, type KvColumn } from './kv';
import { formatStamp } from './cs-labels';

// KAN-237: 고객 연락처 주소로 온 새 메일. 자동 접수하지 않는다. 담당자가 본문을 확인하고 [문의로 등록]을 눌러야
// 문의가 생기고 메일이 그 문의에 연결된다(이후 고객 회신은 회신 흐름으로 붙는다). [문의 아님]은 OTHER로 재분류한다.
const problemText = (error: unknown) => error instanceof ApiError
  ? error.code === 'MAIL_ALREADY_LINKED' ? '이미 다른 업무(회신·RFP)에 연결된 메일입니다.'
    : error.code === 'MAIL_TOO_LONG_FOR_TICKET' ? '본문이 문의 한도(50,000자)를 넘어 등록할 수 없습니다.'
      : error.code === 'MAIL_TRIAGE_VERSION_CONFLICT' ? '그사이 분류가 바뀌었습니다. 새로고침 뒤 다시 확인하세요.' : error.message
  : error instanceof Error && /[가-힣]/.test(error.message) ? error.message : '처리하지 못했습니다.';
const CODE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

export function CsMailCandidates({ enabled, products }: { enabled: boolean; products: string[] }) {
  const mode = getApiMode();
  const candidates = useQuery({ queryKey: ['cs-home', mode, 'mail-candidates'], queryFn: ({ signal }) => listCsCandidates(signal), enabled, retry: false, staleTime: 0 });
  const [open, setOpen] = useState<CsCandidate | null>(null);
  const items = candidates.data ?? [];
  if (!enabled || candidates.isError || items.length === 0) return null;
  const columns: KvColumn<CsCandidate>[] = [
    { key: 'received', header: '받은 시각', width: 130, className: 'c', cell: row => formatStamp(row.received_at) },
    { key: 'from', header: '보낸 사람', width: 220, cell: row => row.from_address },
    { key: 'customer', header: '고객 ID', width: 120, cell: row => row.customer_external_ids.join(', ') },
    { key: 'subject', header: '제목', className: 't', cell: row => row.subject || '(제목 없음)' },
    { key: 'act', header: '', width: 70, className: 'c', cell: row => <button className="kb" onClick={() => setOpen(row)}>확인</button> },
  ];
  return <div className="pane" aria-label="문의로 보이는 메일">
    <div className="ph"><h2>문의로 보이는 메일 {items.length}건</h2><div className="r"><span className="meta">고객 연락처 주소로 온 새 메일 · 확인 후 등록</span></div></div>
    <KvGrid label="문의로 보이는 메일" columns={columns} rows={items} rowKey={row => row.message_id} fallback="문의로 보이는 메일이 없습니다." />
    {open && <Register item={open} products={products} onClose={() => setOpen(null)} />}
  </div>;
}

function Register({ item, products, onClose }: { item: CsCandidate; products: string[]; onClose: () => void }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const content = useQuery({ queryKey: ['mail-content', item.message_id], queryFn: ({ signal }) => getMailContent(item.message_id, signal), retry: false });
  const [customer, setCustomer] = useState(item.customer_external_ids[0] ?? '');
  const [product, setProduct] = useState('');
  const [language, setLanguage] = useState('ko');
  const done = () => void client.invalidateQueries({ queryKey: ['cs-home'] });
  const register = useMutation({
    mutationFn: () => registerTicket(item.message_id, { customer_external_id: customer.trim(), product_code: product.trim(), body_language: language.trim() }),
    onSuccess: data => { done(); onClose(); navigate(`/cs/tickets/${encodeURIComponent(data.ticket_id)}`); },
  });
  const notInquiry = useMutation({
    mutationFn: () => reclassifyMail(item.message_id, { version: item.triage_version, mail_type: 'OTHER', link_suggestion: null, needs_review: false, reason: 'C/S 담당자 확인: 문의 아님' }),
    onSuccess: () => { done(); onClose(); },
  });
  const busy = register.isPending || notInquiry.isPending;
  const ready = CODE.test(customer.trim()) && product.trim() !== '' && language.trim().length >= 2 && content.isSuccess && !content.data.truncated && !busy;
  return <KvModal title="문의로 보이는 메일 확인" onClose={() => !busy && onClose()}>
    <div className="kv-sub" style={{ margin: '0 0 8px' }}>{item.from_address} · {formatStamp(item.received_at)}</div>
    <div className="kv-field"><span>제목</span><div>{content.data?.subject ?? item.subject ?? '(제목 없음)'}</div></div>
    <div className="kv-field"><span>본문</span>
      {content.isPending ? <div>본문을 읽는 중</div> : content.isError ? <div role="alert">{problemText(content.error)}</div>
        : <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 280, overflow: 'auto', margin: 0, font: 'inherit' }}>{content.data.body || '(본문 없음)'}</pre>}
    </div>
    {content.data?.truncated && <p role="alert">본문이 문의 한도(50,000자)를 넘어 등록할 수 없습니다.</p>}
    <div className="kv-field"><span className="req">고객 ID</span>
      {item.customer_external_ids.length > 1
        ? <select aria-label="고객 ID" value={customer} disabled={busy} onChange={event => setCustomer(event.target.value)}>{item.customer_external_ids.map(id => <option key={id}>{id}</option>)}</select>
        : <input aria-label="고객 ID" value={customer} maxLength={64} disabled={busy} onChange={event => setCustomer(event.target.value)} />}
    </div>
    <div className="kv-field"><span className="req">제품 코드</span><input aria-label="제품 코드" list="cs-mail-products" value={product} maxLength={64} disabled={busy} onChange={event => setProduct(event.target.value)} /><datalist id="cs-mail-products">{products.map(code => <option key={code} value={code} />)}</datalist></div>
    <div className="kv-field"><span className="req">본문 언어</span><input aria-label="본문 언어" value={language} maxLength={16} disabled={busy} onChange={event => setLanguage(event.target.value)} /></div>
    {register.isError && <div className="kv-err" role="alert" style={{ margin: '6px 0', padding: '6px 10px' }}>{problemText(register.error)}</div>}
    {notInquiry.isError && <div className="kv-err" role="alert" style={{ margin: '6px 0', padding: '6px 10px' }}>{problemText(notInquiry.error)}</div>}
    <div className="kv-actions">
      <button type="button" className="kb" disabled={busy} onClick={onClose}>닫기</button>
      <button type="button" className="kb" disabled={busy} onClick={() => notInquiry.mutate()}>문의 아님</button>
      <button type="button" className="kb primary" disabled={!ready} onClick={() => register.mutate()}>{register.isPending ? '등록 중' : '문의로 등록'}</button>
    </div>
  </KvModal>;
}
