import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { me, type Identity } from '../api/platform';
import { downloadExport, editDraft, getDraft, getDraftProject, getExportJob, getRewriteJob, sendCommand, SessionChanged, UncertainWrite, type Accepted, type RewriteAccepted, type Command, type Draft, type Review, type Sentence } from '../api/rfp-draft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime } from '../components/tft';
import { KvBox, KvCond, KvCondField, KvCondRow, KvField, KvNext, KvSplit, KvTitle } from '../ls-shell/kv';

import { DATA_ORIGIN_LABEL, JUDGEMENT_LABEL, OUTPUT_LABEL, label } from './labels';
import './RfpDraft.css';

const SECTIONS: Record<string, string> = {
  CUSTOMER_GOAL_SCOPE: '고객 목표와 범위', OUR_RESPONSE: '자사 대응', DEVIATIONS: '차이 사항',
  PARTNER_SUPPLEMENT: '협력사 보완', ASSUMPTIONS_EXCLUSIONS: '가정과 제외', QUESTIONS_NEXT_ACTIONS: '질문과 다음 행동',
};
const EVIDENCE_LABEL: Record<string, string> = { requirement: '요구', clause: '조항', product: '제품', partner: '협력사', question: '질문' };
const JOB_LABEL = { QUEUED: '대기 중', RUNNING: '내보내는 중', SUCCEEDED: '완료', PARTIAL: '일부 완료 — 파일 확인 필요', FAILED: '실패', CANCELLED: '취소됨' };
type Editing = { sentence: Sentence; version: number; text: string; reason: string };

export default function RfpDraft() {
  const { projectId = '' } = useParams();
  const access = useBusinessAccess('tft');
  const cache = useQueryClient();
  const session = useQuery({ queryKey: ['me'], queryFn: me, enabled: access.ready, retry: false });
  const [notice, setNotice] = useState('');
  if (!access.ready) return <BusinessGate access={access} title="초안 및 검토 이력" />;
  if (session.isError) return <main><ErrorState error={session.error} onRetry={() => session.refetch()} /></main>;
  if (session.isPending || !session.data) return <main>{notice && <p role="alert">{notice}</p>}{session.isFetching ? <Loading label="현재 계정 확인 중" /> : <ErrorState error={new ApiError(401, '로그인이 필요합니다.')} />}</main>;
  const user = session.data.data;
  return <DraftWorkspace key={`${user.id}:${projectId}`} projectId={projectId} user={user} notice={notice} onSessionChanged={() => {
    setNotice(new SessionChanged().message); cache.setQueryData(['me'], null); void session.refetch();
  }} />;
}
function DraftWorkspace({ projectId, user, notice, onSessionChanged }: { projectId: string; user: Identity; notice: string; onSessionChanged: () => void }) {
  const cache = useQueryClient();
  const alive = useRef(true);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController(); lifetime.current = controller;
    return () => { alive.current = false; controller.abort(); };
  }, []);
  const [version, setVersion] = useState<number>();
  const [versionInput, setVersionInput] = useState('');
  const [view, setView] = useState('all');
  const [selectedSentenceId, setSelectedSentenceId] = useState<string>();
  const [section, setSection] = useState('all');
  const key = ['rfp-draft', user.id, projectId, version];
  const draft = useQuery({ queryKey: key, queryFn: ({ signal }) => getDraft(projectId, version, signal), retry: false });
  const project = useQuery({ queryKey: ['rfp-draft-project', user.id, projectId], queryFn: ({ signal }) => getDraftProject(projectId, signal), retry: false });
  const canWrite = !!user && !!project.data && !project.isError && user.roles.some(role => ['sales', 'manager', 'dept_staff'].includes(role)) && [project.data.sales_user_id, project.data.responsible_user_id].includes(user.id);
  const [editing, setEditing] = useState<Editing>();
  const [note, setNote] = useState('');
  const [review, setReview] = useState<Review>();
  const [reviewInput, setReviewInput] = useState('');
  const [command, setCommand] = useState<Command>();
  const [accepted, setAccepted] = useState<Accepted>();
  const [rewriteAccepted, setRewriteAccepted] = useState<RewriteAccepted>();
  const refreshedRewrite = useRef<string | undefined>(undefined);
  const [exportReview, setExportReview] = useState<number>();
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState('');
  const [blocked, setBlocked] = useState(false);
  const active = !!accepted;
  const job = useQuery({
    queryKey: ['rfp-draft-export', user.id, projectId, accepted?.job_id],
    queryFn: ({ signal }) => getExportJob(projectId, accepted!.job_id, signal),
    enabled: active, retry: false,
    refetchInterval: query => !query.state.error && (!query.state.data || ['QUEUED', 'RUNNING'].includes(query.state.data.status)) ? 1500 : false,
  });
  const rewriteJob = useQuery({
    queryKey: ['rfp-draft-rewrite', user.id, projectId, rewriteAccepted?.job_id],
    queryFn: ({ signal }) => getRewriteJob(projectId, rewriteAccepted!.job_id, signal),
    enabled: !!rewriteAccepted, retry: false,
    refetchInterval: query => !query.state.error && (!query.state.data || ['QUEUED', 'RUNNING'].includes(query.state.data.status)) ? 1500 : false,
  });
  useEffect(() => {
    const finished = rewriteJob.data;
    if (!finished || !['SUCCEEDED', 'PARTIAL'].includes(finished.status) || refreshedRewrite.current === finished.job_id) return;
    refreshedRewrite.current = finished.job_id;
    setVersion(undefined); setVersionInput('');
    void cache.invalidateQueries({ queryKey: ['rfp-draft', user.id, projectId] });
    setMessage('모델 작성 작업이 끝났습니다. 최신 초안의 모델 작성·양식 문장 표시를 확인하세요. 그사이 저장된 사람 편집이 있으면 모델 결과를 적용하지 않습니다.');
  }, [rewriteJob.data, cache, user.id, projectId]);
  const invalid = (failure: unknown) => {
    if (!alive.current) return;
    if (failure instanceof SessionChanged) { onSessionChanged(); return; }
    setError(failure);
    if (failure instanceof ApiError && [401, 403].includes(failure.status)) setBlocked(true);
  };
  const expiredJob = (job.error instanceof ApiError && [401, 403].includes(job.error.status)) || (rewriteJob.error instanceof ApiError && [401, 403].includes(rewriteJob.error.status));
  const writable = canWrite && !blocked && !expiredJob && !draft.isError && !busy && !command;
  const data = draft.isError ? undefined : draft.data;
  const sentences = data?.sections.flatMap(part => part.sentences);
  const needsReview = sentences?.filter(row => row.needs_rereview).length;
  const matches = (row: Sentence) => view === 'all' || (view === 'review' ? row.needs_rereview : view === 'evidence' ? row.evidence_missing : row.unconfirmed);
  const displayedSections = data?.sections.filter(part => (section === 'all' || section === part.section) && (view === 'all' || part.sentences.some(matches))) ?? [];
  const visibleSentences = displayedSections.flatMap(part => part.sentences.filter(matches));
  const selectedSentence = visibleSentences.find(row => row.sentence_id === selectedSentenceId) ?? visibleSentences[0];
  const latest = version === undefined;
  useEffect(() => {
    if (editing) document.getElementById(`draft-sentence-${editing.sentence.sentence_id}`)?.scrollIntoView?.({ block: 'nearest' });
  }, [editing?.sentence.sentence_id]);
  function startEdit(row: Sentence | undefined) {
    if (!row || !data || !writable || !latest || editing) return;
    setSelectedSentenceId(row.sentence_id);
    setEditing({ sentence: row, version: data.version, text: row.text, reason: '' });
    setError(undefined);
  }
  async function saveEdit() {
    if (!editing || !writable || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(undefined); setMessage('');
    try {
      const result = await editDraft(projectId, user.id, { version: editing.version, edits: [{ sentence_id: editing.sentence.sentence_id, text: editing.text, reason: editing.reason, evidence: editing.sentence.evidence }] }, lifetime.current!.signal);
      if (!alive.current) return;
      cache.setQueryData<Draft>(['rfp-draft', user.id, projectId, undefined], result);
      setVersion(undefined); setVersionInput(''); setEditing(undefined); setMessage(`초안 ${result.version}판을 저장했습니다.`);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && failure.status === 409) setMessage('다른 변경이 있습니다. 입력은 보존했습니다. 최신 조회 후 편집을 취소하고 새 판본에서 다시 선택하세요.');
      invalid(failure);
    } finally { inFlight.current = false; setBusy(false); }
  }
  async function submit(next: Command) {
    if (inFlight.current || !canWrite || blocked || expiredJob) return;
    inFlight.current = true; setBusy(true); setCommand(next); setError(undefined); setMessage('');
    try {
      const result = await sendCommand(projectId, next, lifetime.current!.signal);
      if (!alive.current) return;
      if (next.kind === 'review') {
        const saved = result as Review; setReview(saved); setReviewInput(String(saved.review_version));
        setMessage(`검토본 ${saved.review_version}판을 저장했습니다. 발송하거나 제안을 확정한 상태가 아닙니다.`);
      } else if (next.kind === 'rewrite') { setRewriteAccepted(result as RewriteAccepted); }
      else { setAccepted(result as Accepted); setExportReview(next.body.review_version); }
      setCommand(undefined);
    } catch (failure) {
      if (!alive.current) return;
      if (!(failure instanceof UncertainWrite) && !(command && failure instanceof ApiError && [401, 403].includes(failure.status))) setCommand(undefined);
      invalid(failure);
    } finally { inFlight.current = false; setBusy(false); }
  }
  async function download() {
    if (!accepted || job.data?.status !== 'SUCCEEDED' || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(undefined);
    try {
      const blob = await downloadExport(projectId, accepted.export_id);
      if (!alive.current) return;
      const url = URL.createObjectURL(blob); const link = document.createElement('a');
      link.href = url; link.download = `rfp-${accepted.export_id}.docx`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) { invalid(failure); }
    finally { inFlight.current = false; setBusy(false); }
  }
  const sentenceNumbers = new Map(sentences?.map((row, index) => [row.sentence_id, index + 1]));
  const lastReview = review?.review_version ?? project.data?.review_version;
  const editingVisible = data?.sections.some(part => (section === 'all' || section === part.section) && part.sentences.some(row => matches(row) && row.sentence_id === editing?.sentence.sentence_id));
  const editor = editing && <form className="draft-editor" aria-label="문장 편집" onSubmit={event => { event.preventDefault(); void saveEdit(); }}>
    <label><span>수정 문장</span><textarea required maxLength={4000} rows={3} value={editing.text} disabled={busy} onChange={event => setEditing({ ...editing, text: event.target.value })} /></label>
    <label><span>수정 사유</span><textarea required maxLength={500} rows={2} value={editing.reason} disabled={busy} onChange={event => setEditing({ ...editing, reason: event.target.value })} /></label>
    <p>초안 {editing.version}판 기준 · 근거·조건은 보존됩니다. 고쳐 저장한 문장만 다시 확인합니다.</p>
    <div className="draft-editor-actions"><button className="kb" type="button" disabled={busy} onClick={() => setEditing(undefined)}>편집 취소</button>{' '}
    <button className="kb primary" disabled={!writable || !editing.text.trim() || !editing.reason.trim()}>문장 저장</button></div>
  </form>;
  return <main className="rfp-draft">
    <KvTitle path="제안 지원" title="초안 및 검토 이력" code="TF0104">
      <button className="kb" disabled={!writable || !data || !latest || !!editing || !!command || rewriteJob.isFetching || (!!rewriteJob.data && ['QUEUED', 'RUNNING'].includes(rewriteJob.data.status))} onClick={() => submit({ actorId: user.id, kind: 'rewrite', key: crypto.randomUUID(), body: { version: data!.version } })}>모델로 문장 작성</button>
      <button className="kb" aria-label="선택 문장 수정" title={selectedSentence ? `S${sentenceNumbers.get(selectedSentence.sentence_id)} 문장 수정` : '표시된 문장이 없습니다.'} disabled={!writable || !latest || !!editing || !selectedSentence} onClick={() => startEdit(selectedSentence)}>문장 수정</button>
      <Link to={`/tft/projects/${projectId}`}>프로젝트</Link>{' '}
      <button className="kb" disabled={busy || draft.isFetching} onClick={() => { setVersion(undefined); setVersionInput(''); void cache.invalidateQueries({ queryKey: ['rfp-draft', user.id, projectId] }); }}>최신 조회</button>
    </KvTitle>
    <KvNext>{data ? `초안 판본 ${data.version} · 문장 ${sentences?.length}개 · 재검토 ${needsReview}개. ` : ''}근거와 조건을 검토한 뒤 검토본을 저장하세요. 공급·납기 확약과 발송은 사람이 별도로 확인합니다.</KvNext>
    <p className="draft-context">판단을 확인한 뒤 요청하세요. 모델로 문장 작성은 호출 비용이 드는 별도 작업입니다. 실패한 문장은 양식 초안으로 유지됩니다. 저장된 사람 문장은 바꾸지 않습니다.</p>
    {rewriteAccepted && <div className="draft-notice" aria-label="모델 문장 작성 작업">
      {rewriteJob.isPending && <Loading label="모델 작성 상태 확인 중" />}
      {rewriteJob.isError && <ErrorState error={rewriteJob.error} onRetry={() => rewriteJob.refetch()} />}
      {!rewriteJob.isError && rewriteJob.data && <p role="status">모델 문장 작성: {rewriteJob.data.status === 'RUNNING' ? '작성 중' : rewriteJob.data.status === 'QUEUED' ? '대기 중' : rewriteJob.data.status === 'SUCCEEDED' ? '작업 완료' : rewriteJob.data.status === 'PARTIAL' ? '일부 완료·최신 초안 확인' : rewriteJob.data.status === 'CANCELLED' ? '취소됨·양식 초안 유지' : '실패·양식 초안 유지'}</p>}
    </div>}
    {notice && <p className="draft-notice" role="alert">{notice}</p>}
    {project.isError && <ErrorState error={project.error} onRetry={() => project.refetch()} />}
    {!canWrite && <p className="draft-notice">조회 전용입니다. 담당 sales·책임자 계정 확인 후 편집할 수 있습니다.</p>}
    {error != null && <ErrorState error={error} />}
    {message && <p className="draft-notice" role="status">{message}</p>}
    {command && <KvBox title="요청 결과 재확인">
      <p>전송한 내용과 요청 키를 보존했습니다. 결과를 확인하기 전에는 새 요청을 만들 수 없습니다.</p>
      <button className="kb" disabled={busy || blocked} onClick={() => submit(command)}>같은 요청 다시 확인</button>
    </KvBox>}
    <KvCond><KvCondRow columns={2}>
      <KvCondField label="보기"><select aria-label="보기" value={view} disabled={!data} onChange={event => setView(event.target.value)}>
        <option value="all">전체 문장 {sentences?.length ?? '-'}</option>
        <option value="review">재검토 문장만 {needsReview ?? '-'}</option>
        <option value="evidence">근거 없는 문장만 {sentences?.filter(row => row.evidence_missing).length ?? '-'}</option>
        <option value="unconfirmed">미확약 문장만 {sentences?.filter(row => row.unconfirmed).length ?? '-'}</option>
      </select></KvCondField>
      <KvCondField label="구역"><select aria-label="구역" value={section} disabled={!data} onChange={event => setSection(event.target.value)}><option value="all">전체</option>{Object.entries(SECTIONS).map(([key, title]) => <option key={key} value={key}>{title}</option>)}</select></KvCondField>
    </KvCondRow></KvCond>
    <div className="draft-overview">
      <KvField label="프로젝트">{project.data?.name ?? projectId}</KvField>
      <KvField label="초안">{data ? `판본 ${data.version} · ${formatDateTime(data.known_at)}` : '조회 전'}</KvField>
      <KvField label="검토본">{lastReview ? `판본 ${lastReview}` : lastReview === null ? '저장 전' : '조회값 없음'}</KvField>
      <KvField label="근거 없는 문장"><span className={data?.evidence_missing_count ? 'draft-attention' : ''}>{data ? `${data.evidence_missing_count}문장` : '-'}</span></KvField>
    </div>
    <p className="draft-context">보기·구역 필터는 표시만 바꿉니다. 편집과 검토본 저장은 숨겨진 문장까지 포함한 전체 초안 판본을 기준으로 처리합니다.</p>
    {draft.isPending && <Loading />}
    {draft.isError && <ErrorState error={draft.error} onRetry={() => draft.refetch()} />}
    <div className="draft-layout"><KvSplit columns="minmax(0, 1.49fr) minmax(330px, 1fr)">
    <div className="draft-sections" aria-label="초안 문장 목록">
    {data && visibleSentences.length === 0 && <p className="draft-empty" role="status">선택한 조건의 문장이 없습니다.</p>}
    {visibleSentences.length > 0 && displayedSections.map(part => <KvBox key={part.section} title={SECTIONS[part.section] ?? part.section} note={`${part.sentences.filter(matches).length}문장`} bodyStyle={{ padding: 0 }}>
      {!part.sentences.length && <p className="draft-empty">저장된 문장이 없습니다.</p>}
      {part.sentences.filter(matches).map(row => <article className={`draft-sentence${editing?.sentence.sentence_id === row.sentence_id ? ' is-editing' : selectedSentence?.sentence_id === row.sentence_id ? ' is-selected' : ''}`} id={`draft-sentence-${row.sentence_id}`} key={row.sentence_id} aria-label={`문장 ${row.sentence_id}`}>
        <button type="button" className="draft-sentence-number" aria-label={`S${sentenceNumbers.get(row.sentence_id)} 선택`} aria-pressed={selectedSentence?.sentence_id === row.sentence_id} onClick={() => setSelectedSentenceId(row.sentence_id)}>S{sentenceNumbers.get(row.sentence_id)}</button>
        <div className="draft-sentence-body">
          {editing?.sentence.sentence_id === row.sentence_id ? editor : <>
            <p className="draft-sentence-text">{row.text}</p>
            <button className="kb sm draft-edit-button" disabled={!writable || !latest || !!editing} onClick={() => startEdit(row)}>문장 수정</button>
          </>}
        </div>
        <div className="draft-proof">
          <p>{row.provenance_kind === 'human_input' ? '사람 수정' : row.provenance_kind === 'ai_draft' ? '모델 작성' : '양식 문장'}</p>
          <p className={row.needs_rereview || row.evidence_missing ? 'draft-attention' : 'draft-state'}>{[row.unconfirmed && '미확약·미확인', row.needs_rereview && '재검토 필요', row.evidence_missing && '근거 누락'].filter(Boolean).join(' / ')}</p>
          <p className="draft-proof-preview">{Object.entries(EVIDENCE_LABEL).map(([kind, label]) => {
            const count = row.evidence.filter(e => e.type === kind).length;
            return count ? `${label} ${count}건` : '';
          }).filter(Boolean).join(' · ')}</p>
          <details><summary>근거 {row.evidence.length}건 · 조건 {row.conditions.length}건</summary>
            <KvField label="문장 ID"><span>{row.sentence_id}</span></KvField>
            <KvField label="근거">{row.evidence.length ? <ul>{row.evidence.map((e, i) => <li key={`${e.type}:${e.id}:${i}`}>{e.type}: {e.id}</li>)}</ul> : '저장된 근거 없음'}</KvField>
            {row.conditions.length > 0 && <KvField label="조건"><ul>{row.conditions.map((condition, i) => <li key={i} style={{ whiteSpace: 'pre-wrap' }}>{condition}</li>)}</ul></KvField>}
          </details>
        </div>
      </article>)}
    </KvBox>)}
    {editing && !editingVisible && <KvBox title="문장 편집" note="표시 필터 밖의 선택 문장을 편집하고 있습니다.">{editor}</KvBox>}
    </div><aside className="draft-actions" aria-label="검토본과 내보내기">
    <KvBox title="검토본 저장 조건">
      <dl className="draft-checks">
        <div><dt>필수 요구, 상태와 근거 또는 다음 행동</dt><dd>{project.data?.unresolved_mandatory_count === undefined ? '서버 확인' : `미해결 ${project.data.unresolved_mandatory_count}`}</dd></div>
        {project.data?.integration_responsibility?.required && project.data.integration_responsibility.holder === 'UNRESOLVED' && <div><dt>통합 책임</dt><dd className="draft-attention">미확보, <Link to={`/tft/projects/${projectId}/supplement`}>보완 화면에서 지정</Link></dd></div>}
        <div><dt>근거 없는 문장</dt><dd className={data?.evidence_missing_count ? 'draft-attention' : ''}>{data ? `${data.evidence_missing_count}문장` : '조회 전'}</dd></div>
        <div><dt>재검토 필요 문장</dt><dd className={needsReview ? 'draft-attention' : ''}>{needsReview === undefined ? '조회 전' : `${needsReview}문장`}</dd></div>
        <div><dt>초안 판본</dt><dd>{data ? `${data.version} · ${formatDateTime(data.known_at)}` : '조회 전'}</dd></div>
        <div><dt>마지막 검토본</dt><dd>{lastReview ? `판본 ${lastReview}` : lastReview === null ? '저장 전' : '조회값 없음'}</dd></div>
      </dl>
      <p className="draft-help">필수 요구의 근거 또는 다음 행동과 통합 책임은 저장 시 서버에서 확인합니다.</p>
      <p className="draft-help">고쳐 저장한 문장만 재검토가 풀립니다. 요구에 재검토가 남아 있으면 그 문장은 남습니다.</p>
      {data && <p className="draft-disclaimer" role="note">{data.disclaimer} <span>{label(DATA_ORIGIN_LABEL, data.data_origin)}</span></p>}
    </KvBox>
    <KvBox title="검토본 저장" note="전체 초안 판본 기준">
      <label className="draft-input">검토 메모<textarea rows={2} maxLength={1000} value={note} disabled={busy || !!command} onChange={event => setNote(event.target.value)} /></label>
      <button className="kb primary" disabled={!writable || !data || !latest || !!editing} onClick={() => submit({ actorId: user.id, kind: 'review', key: crypto.randomUUID(), body: { version: data!.version, note: note || null } })}>검토본 저장</button>
      {review && <div className="draft-receipt"><KvField label="저장한 검토본">{review.review_version}판 (초안 {review.draft_version}판)</KvField><KvField label="저장 일시">{formatDateTime(review.saved_at)}</KvField><KvField label="출력 상태">{label(OUTPUT_LABEL, review.output_status)} / {label(JUDGEMENT_LABEL, review.judgement_status)}</KvField></div>}
    </KvBox>
    <KvBox title="DOCX 내보내기" note="저장된 검토본 판본 지정">
      <label className="draft-input">내보낼 검토본 판본<input type="number" min="1" step="1" value={reviewInput} disabled={busy || !!command} onChange={event => setReviewInput(event.target.value)} /></label>
      <div className="draft-buttons"><button className="kb primary" disabled={!writable || !Number.isSafeInteger(Number(reviewInput)) || Number(reviewInput) < 1 || !!editing || job.isFetching || (!!job.data && ['QUEUED', 'RUNNING'].includes(job.data.status))} onClick={() => submit({ actorId: user.id, kind: 'export', key: crypto.randomUUID(), body: { review_version: Number(reviewInput), format: 'DOCX' } })}>DOCX 내보내기</button>{' '}
      <button className="kb" disabled title="PDF 내보내기는 아직 지원하지 않습니다.">PDF (미지원)</button></div>
      {accepted && <div className="draft-receipt">
        <KvField label="요청한 검토본">{exportReview}판</KvField>
        <KvField label="내보내기 ID">{accepted.export_id}</KvField>
        {job.isPending && <Loading label="내보내기 상태 확인 중" />}
        {job.isError && <ErrorState error={job.error} onRetry={() => job.refetch()} />}
        {!job.isError && job.data && <p role="status">{JOB_LABEL[job.data.status]}</p>}
        {job.data && ['FAILED', 'PARTIAL', 'CANCELLED'].includes(job.data.status) && <p>파일을 다운로드할 수 없습니다. 상태를 확인한 후 새 내보내기를 요청하세요.</p>}
        <button className="kb" disabled={busy || blocked || job.isError || job.data?.status !== 'SUCCEEDED'} onClick={download}>DOCX 다운로드</button>
      </div>}
    </KvBox>
    <KvBox title="판본 및 검토 이력" note="이력 목록 미제공">
      <p className="draft-help">판본·검토본·내보내기 이력 목록은 제공되지 않습니다.</p>
      <form className="draft-version-form" onSubmit={event => { event.preventDefault(); setVersion(versionInput ? Number(versionInput) : undefined); }}>
        <label className="draft-input">조회할 초안 판본 (비우면 최신)<input type="number" min="1" step="1" value={versionInput} onChange={event => setVersionInput(event.target.value)} /></label>
        <button className="kb" disabled={busy || !!editing || !!command}>판본 조회</button>
      </form>
      {!latest && <p className="draft-help">지정 판본 조회 중입니다. 편집·검토본 저장은 최신 판본에서 진행하세요.</p>}
    </KvBox>
    </aside></KvSplit></div>
  </main>;
}
