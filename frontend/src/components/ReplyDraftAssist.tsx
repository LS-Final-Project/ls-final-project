import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import type { ReplyAssist } from '../api/cs-workflow';
import { ElloMark, ElloWorking } from './Ello';

// 회신 초안 AI 보조(KAN-251). 어느 것도 보내지 않는다. 결과는 담당자가 넣기를 눌러야 회신 상자에 들어가고, 보내기 확인에서 승인해야 나간다.
type Call = (signal: AbortSignal) => Promise<{ data: ReplyAssist }>;
type Failure = { state: 'failed'; text: string; off?: boolean };
const MASKED = /\[[A-Z]+_PROVIDED\]/;

// 꺼진 배포(404)·권한(403)은 기능 이름을 넣어 안내하고, 나머지는 서버 코드별 한국어 안내(ApiError.message)를 쓴다.
function failure(error: unknown, name: string): Failure {
  if (error instanceof ApiError && error.status === 404) return { state: 'failed', text: `${name} 기능이 이 배포에서 꺼져 있습니다.`, off: true };
  if (error instanceof ApiError && error.status === 403) return { state: 'failed', text: `${name}은 C/S 담당자만 쓸 수 있습니다.`, off: true };
  return { state: 'failed', text: error instanceof Error ? error.message : `${name}을 하지 못했습니다.` };
}
const maskedNote = (text: string) => MASKED.test(text) && <div className="meta">가림 표시([…_PROVIDED])는 보내기 전에 실제 값으로 바꾸세요.</div>;

/** 확인한 부서 답변 상자 안의 "부서 답변으로 AI 회신 초안". 결과를 보여 주고 "이 초안 쓰기"로만 회신 상자에 넣는다. */
export function DeptReplyDraft({ create, load, onUse, inUse = false }: { create: Call; load?: (signal: AbortSignal) => Promise<{ data: ReplyAssist | null }>; onUse: (text: string) => void; inUse?: boolean }) {
  // 회신 상자가 이 초안을 그대로 들고 있으면 같은 글을 두 번 보이지 않게 한 줄로 접는다.
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState<{ state: 'idle' | 'busy' } | Failure | { state: 'done'; data: ReplyAssist }>({ state: 'idle' });
  // 저장된 초안이 있으면 처음부터 보인다(모델 호출 없음). 꺼진 배포(404)면 단추를 미리 막는다. 그 밖의 읽기 실패는 단추를 그대로 둔다.
  useEffect(() => {
    if (!load) return;
    const controller = new AbortController();
    // 읽기가 늦게 오면 그 사이 "만들기"로 시작한 상태를 덮지 않는다(아직 idle일 때만 반영).
    const settle = (next: typeof draft) => setDraft(prev => prev.state === 'idle' ? next : prev);
    load(controller.signal).then(result => { if (result.data) settle({ state: 'done', data: result.data }); },
      error => { if (!controller.signal.aborted && error instanceof ApiError && (error.status === 404 || error.status === 403)) settle(failure(error, 'AI 회신 초안')); });
    return () => controller.abort();
    // 부모가 부서 답변마다 key를 바꿔 새로 그린다. 처음 한 번만 읽는다.
  }, []);
  const make = () => {
    setDraft({ state: 'busy' });
    create(new AbortController().signal).then(result => setDraft({ state: 'done', data: result.data }), error => setDraft(failure(error, 'AI 회신 초안')));
  };
  if (draft.state === 'done' && inUse && !expanded) return <div className="assist-row">
    <span className="meta"><ElloMark size={14} />부서 답변 기반 AI 초안을 회신 상자에 넣었습니다</span>
    <button className="kb3 sm ghost" aria-expanded={false} onClick={() => setExpanded(true)}>다시 보기</button>
  </div>;
  if (draft.state === 'done') return <div className="dept-reply" aria-label="부서 답변 기반 AI 초안">
    <span><ElloMark size={14} />부서 답변 기반 AI 초안 <small className="meta">{draft.data.reused ? '저장본' : '방금 만듦'} · 확정 약속 없이 씀 · 보내기 전 확인</small></span>
    {draft.data.text}
    {maskedNote(draft.data.text)}
    {inUse ? <button className="kb3 sm ghost" aria-expanded={true} onClick={() => setExpanded(false)}>접기</button>
      : <button className="kb3 sm" onClick={() => { setExpanded(false); onUse(draft.data.text); }}>이 초안 쓰기</button>}
  </div>;
  return <div className="assist-row">
    <button className="kb3 sm" disabled={draft.state === 'busy' || (draft.state === 'failed' && draft.off)} title="부서 답변만 근거로 고객 언어 초안을 씁니다. 모델을 한 번 호출합니다(비용). 같은 답변이면 저장본을 씁니다" onClick={make}><ElloMark />부서 답변으로 AI 회신 초안</button>
    {draft.state === 'busy' && <span className="meta" aria-busy="true"><ElloWorking size={18} />부서 답변으로 고객 언어 초안을 쓰는 중…</span>}
    {draft.state === 'failed' && <span className="meta unc" role="alert">{draft.text}</span>}
  </div>;
}

/**
 * 회신 상자 바로 아래의 "한국어로 보기"와 "한국어로 고쳐 반영". text는 지금 회신 상자의 고객 언어 글이다.
 * 번역 뒤 회신 상자의 글이 바뀌면 번역은 오래된 것으로 표시하고 반영을 막는다. 반영 결과는 "회신 상자에 넣기"를 눌러야 들어간다.
 */
export function DraftKorean({ text, language, translate, apply, onAccept }: {
  text: string;
  language: string;
  translate: (text: string, signal: AbortSignal) => Promise<{ data: ReplyAssist }>;
  apply: (text: string, editedKo: string, signal: AbortSignal) => Promise<{ data: ReplyAssist }>;
  onAccept: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [ko, setKo] = useState<{ state: 'idle' | 'busy' } | Failure | { state: 'done'; source: string; text: string }>({ state: 'idle' });
  const [edit, setEdit] = useState<string | null>(null);
  const [back, setBack] = useState<{ state: 'idle' | 'busy' } | Failure | { state: 'done'; data: ReplyAssist }>({ state: 'idle' });
  const off = ko.state === 'failed' && ko.off;
  const stale = ko.state === 'done' && ko.source !== text;
  const run = () => {
    const source = text;
    setKo({ state: 'busy' }); setEdit(null); setBack({ state: 'idle' });
    translate(source, new AbortController().signal).then(result => setKo({ state: 'done', source, text: result.data.text }), error => setKo(failure(error, '초안 번역')));
  };
  const toggle = () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (ko.state === 'idle' || (ko.state === 'done' && stale)) run();
  };
  const submit = () => {
    if (ko.state !== 'done' || edit === null) return;
    setBack({ state: 'busy' });
    apply(ko.source, edit, new AbortController().signal).then(result => setBack({ state: 'done', data: result.data }), error => setBack(failure(error, '한국어 수정 반영')));
  };
  // 넣으면 번역 칸을 닫는다. 회신 상자의 글이 바뀌었으니 다시 열면 지금 글로 다시 번역한다(같은 글이면 저장본).
  const accept = (value: string) => { onAccept(value); setEdit(null); setBack({ state: 'idle' }); setOpen(false); };
  const unchanged = ko.state === 'done' && edit !== null && edit.trim() === ko.text.trim();

  return <div className="draft-ko">
    <div className="assist-row">
      <button className={open ? 'kb3 sm on' : 'kb3 sm'} aria-pressed={open} disabled={off || !text.trim()} title="회신 상자의 초안을 한국어로 옮깁니다. 처음 한 번 모델을 호출합니다(비용). 같은 글이면 저장본을 씁니다" onClick={toggle}><ElloMark />{open ? '한국어 숨기기' : '한국어로 보기'}</button>
      {off && <span className="meta unc">{ko.text}</span>}
    </div>
    {open && !off && <div className="ko-box" aria-label="초안 한국어 번역">
      {ko.state === 'busy' ? <div className="sk-lines" aria-busy="true"><span><ElloWorking />초안을 한국어로 옮기는 중…</span><i /><i /></div>
        : ko.state === 'failed' ? <div className="meta unc" role="alert">{ko.text} <button className="kb3 sm" onClick={run}>다시 번역</button></div>
        : ko.state === 'done' ? <>
          <div className="meta"><span className="ai"><ElloMark size={14} />AI 한국어 번역</span> 읽기용입니다. 고객에게는 {language} 초안이 나갑니다.</div>
          {stale && <div className="stale">회신 상자의 초안이 번역 뒤에 바뀌었습니다. 지금 초안으로 다시 번역해야 고쳐 반영할 수 있습니다. <button className="kb3 sm" onClick={run}>다시 번역</button></div>}
          {edit === null
            ? <><div className="ko-text">{ko.text}</div>
              {!stale && <div className="assist-row"><button className="kb3 sm" title="한국어로 고친 뒤 고객 언어 초안에 반영합니다" onClick={() => { setEdit(ko.text); setBack({ state: 'idle' }); }}>한국어로 고쳐 반영</button></div>}</>
            : <>
              <textarea className="edit-body" aria-label="한국어 수정" rows={6} value={edit} onChange={event => { setEdit(event.target.value); setBack({ state: 'idle' }); }} />
              <div className="assist-row">
                <span className="meta">{unchanged ? '고친 곳이 없습니다' : `고친 부분만 ${language}로 바꿉니다. 모델 1회(비용)`}</span>
                <button className="kb3 sm" style={{ marginLeft: 'auto' }} disabled={back.state === 'busy'} onClick={() => { setEdit(null); setBack({ state: 'idle' }); }}>고치기 취소</button>
                <button className="kb3 sm pri" disabled={stale || unchanged || !edit.trim() || back.state === 'busy'} onClick={submit}>{back.state === 'busy' ? '반영하는 중' : `${language}로 반영`}</button>
              </div>
            </>}
          {back.state === 'busy' && <div className="sk-lines" aria-busy="true"><span><ElloWorking />고친 한국어를 {language} 초안에 반영하는 중…</span><i /><i /></div>}
          {back.state === 'failed' && <div className="meta unc" role="alert">{back.text}</div>}
          {back.state === 'done' && <div className="ko-result" aria-label="반영 결과">
            <div className="meta"><b style={{ color: '#1d2433' }}>반영 결과 ({language})</b> <small>아직 회신 상자에 넣지 않았습니다. 읽고 넣으세요</small></div>
            {back.data.commitment_warning && <div className="note err" role="alert">승인·확정 약속 문구가 있습니다. 담당자가 확정한 내용인지 확인한 뒤 넣으세요.</div>}
            <div className="ko-text">{back.data.text}</div>
            {maskedNote(back.data.text)}
            <div className="assist-row"><button className="kb3 sm" style={{ marginLeft: 'auto' }} onClick={() => setBack({ state: 'idle' })}>버리기</button><button className="kb3 sm pri" onClick={() => accept(back.data.text)}>회신 상자에 넣기</button></div>
          </div>}
        </> : null}
    </div>}
  </div>;
}
