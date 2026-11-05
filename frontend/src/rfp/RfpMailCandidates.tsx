import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { downloadMailAttachment, getMailTriage, linkMailRfpProject, listRfpCandidates, reclassifyMail, type RfpCandidate } from '../api/mail';
import { createProject, uploadDocument } from '../api/rfp';
import { constraintsBody, emptyConstraints } from './ProjectConstraintsForm';

// KAN-73: 받은 메일 가운데 RFP로 보이는 메일. 분류는 추천일 뿐이고, 담당 영업이 확인하고 [RFP로 등록]을 눌러야
// 프로젝트를 만들고 첨부를 올린 뒤 메일을 연결한다. 분석은 시작하지 않는다(프로젝트 화면에서 시작).
const problemText = (error: unknown) => error instanceof ApiError
  ? error.code === 'MAIL_RFP_ALREADY_REGISTERED' ? '이미 다른 프로젝트로 등록한 메일입니다.' : error.message
  : error instanceof Error && /[가-힣]/.test(error.message) ? error.message : '처리하지 못했습니다.';

export function RfpMailCandidates({ enabled }: { enabled: boolean }) {
  const mode = getApiMode();
  const candidates = useQuery({ queryKey: ['rfp-candidates', mode], queryFn: ({ signal }) => listRfpCandidates(signal), enabled, retry: false });
  const [open, setOpen] = useState<string | null>(null);
  const items = candidates.data ?? [];
  if (!enabled || candidates.isError || items.length === 0) return null;
  return <section className="kv-box" aria-label="RFP로 보이는 메일"><h2>RFP로 보이는 메일<small>{items.length}건 · 확인 후 등록</small></h2>
    <div className="body" style={{ padding: 0 }}><ul className="rfp-today">{items.map(item => <li key={item.message_id}>
      <span>{item.subject || '(제목 없음)'}</span>
      <span className="muted">{item.from_address ?? ''}{item.basis ? ` · ${item.basis}` : ''}{item.attachments.length === 0 ? ' · DOCX·PDF 첨부 없음' : ''}</span>
      <button className="kb" onClick={() => setOpen(open === item.message_id ? null : item.message_id)}>{open === item.message_id ? '닫기' : '확인'}</button>
      {open === item.message_id && <Register item={item} onDone={() => setOpen(null)} />}
    </li>)}</ul></div>
  </section>;
}

function Register({ item, onDone }: { item: RfpCandidate; onDone: () => void }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [attachment, setAttachment] = useState(item.attachments[0]?.id ?? '');
  const [name, setName] = useState((item.subject ?? '').replace(/^\s*(re|fw|fwd)\s*:\s*/i, '').trim().slice(0, 200));
  const [code, setCode] = useState('');
  // 실패 뒤 다시 눌러도 프로젝트를 두 번 만들지 않도록 만든 프로젝트·올린 문서를 기억한다.
  const made = useRef<{ key: string; project?: string; uploaded?: boolean }>({ key: crypto.randomUUID() });
  const register = useMutation({
    mutationFn: async () => {
      const chosen = item.attachments.find(a => a.id === attachment);
      if (!chosen) throw new ApiError(422, 'DOCX·PDF 첨부가 없어 등록할 수 없습니다.');
      const file = await downloadMailAttachment(item.message_id, chosen);
      const state = made.current;
      if (!state.project) state.project = (await createProject({ ...constraintsBody(emptyConstraints), name: name.trim(), company_code: code.trim(), data_origin: 'synthetic', purpose: 'INITIAL_PROPOSAL' }, state.key)).data.id;
      if (!state.uploaded) {
        await uploadDocument(state.project, { file, kind: 'RFP_SPEC', title: name.trim(), note: '', data_origin: 'synthetic' }, `${state.key}-doc`);
        state.uploaded = true;
      }
      await linkMailRfpProject(item.message_id, state.project);
      return state.project;
    },
    onSuccess: id => { client.invalidateQueries({ queryKey: ['rfp-candidates'] }); client.invalidateQueries({ queryKey: ['projects'] }); navigate(`/tft/projects/${id}`); },
  });
  const notRfp = useMutation({
    mutationFn: async () => {
      const triage = await getMailTriage(item.message_id);
      await reclassifyMail(item.message_id, { version: triage.version, mail_type: 'OTHER', link_suggestion: null, needs_review: false, reason: '담당 영업 확인: RFP 아님' });
    },
    onSuccess: () => { client.invalidateQueries({ queryKey: ['rfp-candidates'] }); onDone(); },
  });
  const ready = !!attachment && name.trim() !== '' && code.trim() !== '' && !register.isPending && !notRfp.isPending;
  return <div className="ws-ready" style={{ flexBasis: '100%' }}>
    {item.attachments.length === 0 ? <p role="alert">DOCX·PDF 첨부가 없어 RFP로 등록할 수 없습니다.</p>
      : <label className="ws-field">올릴 첨부<select value={attachment} onChange={event => setAttachment(event.target.value)}>
        {item.attachments.map(a => <option key={a.id} value={a.id}>{a.name ?? a.id}</option>)}</select></label>}
    <label className="ws-field">프로젝트 이름<input value={name} maxLength={200} onChange={event => setName(event.target.value)} /></label>
    <label className="ws-field">고객 코드<input value={code} maxLength={40} onChange={event => setCode(event.target.value)} /></label>
    <p className="muted">등록하면 프로젝트를 만들고 첨부를 올립니다. 분석은 프로젝트 화면에서 따로 시작합니다.</p>
    <span>
      <button className="kb primary" disabled={!ready} onClick={() => register.mutate()}>RFP로 등록</button>{' '}
      <button className="kb" disabled={register.isPending || notRfp.isPending} onClick={() => notRfp.mutate()}>RFP 아님</button>
    </span>
    {register.isError && <p role="alert">{problemText(register.error)}</p>}
    {notRfp.isError && <p role="alert">{problemText(notRfp.error)}</p>}
  </div>;
}
