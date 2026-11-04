import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { me, type Identity } from '../api/platform';
import { downloadExport, editDraft, getDraft, getDraftProject, getExportJob, getExports, getReviewVersions, getRewriteJob, getRewriteSummary, sendCommand, SessionChanged, UncertainWrite, type Accepted, type ExportRow, type RewriteAccepted, type RewriteSummary, type Command, type Draft, type Review, type ReviewRow, type Sentence } from '../api/rfp-draft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvBox, KvCond, KvCondField, KvCondRow, KvField, KvGrid, KvNext, KvSplit, KvTitle, type KvColumn } from '../ls-shell/kv';

import { DATA_ORIGIN_LABEL, JUDGEMENT_LABEL, OUTPUT_LABEL, label, shortDateTime } from './labels';
import './RfpDraft.css';

const SECTIONS: Record<string, string> = {
  CUSTOMER_GOAL_SCOPE: '고객 목표와 범위', OUR_RESPONSE: '자사 대응', DEVIATIONS: '차이 사항',
  PARTNER_SUPPLEMENT: '협력사 보완', ASSUMPTIONS_EXCLUSIONS: '가정과 제외', QUESTIONS_NEXT_ACTIONS: '질문과 다음 행동',
};
const SOURCE_LABEL: Record<string, string> = { system_record: '자동 정리', ai_draft: 'AI 작성', human_input: '직접 입력' };
const EVIDENCE_LABEL: Record<string, string> = { requirement: '요구', clause: '조항', product: '제품', partner: '협력사', question: '질문' };
const JOB_LABEL: Record<string, string> = { QUEUED: '대기 중', RUNNING: '내보내는 중', SUCCEEDED: '완료', PARTIAL: '일부 완료, 파일 확인 필요', FAILED: '실패', CANCELLED: '취소됨' };
// tft는 끝난 내보내기만 기록한다. 실패했거나 아직 도는 작업은 둘 다 QUEUED로 온다.
const EXPORT_LABEL: Record<string, string> = { SUCCEEDED: '완료', QUEUED: '완료 기록 없음' };
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
// 서버에 아직 없는 경로(404 NOT_FOUND·405)다. 프로젝트가 없을 때의 404는 고유 코드가 붙어 여기에 걸리지 않는다.
const unsupported = (failure: unknown) => failure instanceof ApiError && (failure.status === 405 || (failure.status === 404 && (!failure.code || failure.code === 'NOT_FOUND')));
// S 번호를 다섯 개까지 적고 나머지는 개수로 줄인다.
function sentenceList(numbers: number[]) {
  const head = numbers.slice(0, 5).map(no => `S${no}`).join('·');
  return numbers.length > 5 ? `${head} 외 ${numbers.length - 5}문장` : head;
}

type Token = { type: string; id: string; text?: string; count: number };
// 근거를 종류 순서로 묶는다. 번호가 없는 근거는 내부 ID 대신 종류 이름과 개수만 남긴다.
function evidenceTokens(row: Sentence, labels: Record<string, string>): Token[] {
  const seen = new Set<string>();
  const tokens: Token[] = [];
  for (const type of [...Object.keys(EVIDENCE_LABEL), ...new Set(row.evidence.map(e => e.type))]) {
    if (seen.has(type)) continue;
    seen.add(type);
    const items = row.evidence.filter(e => e.type === type);
    const named = new Map<string, string>();
    let unnamed = 0;
    for (const e of items) {
      const text = labels[e.id];
      if (text && !UUID.test(text)) named.set(e.id, text); else unnamed += 1;
    }
    tokens.push(...[...named].map(([id, text]) => ({ type, id, text, count: 1 })));
    if (unnamed) tokens.push({ type, id: `${type}:unnamed`, count: unnamed });
  }
  return tokens;
}

function SentenceProof({ row, labels, projectId }: { row: Sentence; labels: Record<string, string>; projectId: string }) {
  const [allEvidence, setAllEvidence] = useState(false);
  const [allConditions, setAllConditions] = useState(false);
  const tokens = evidenceTokens(row, labels);
  const shown = allEvidence ? tokens : tokens.slice(0, 6);
  const conditions = row.conditions.filter(text => text.trim());
  return <div className="draft-proof">
    {tokens.length > 0 && <p className="draft-evidence">근거 {shown.map((token, index) => {
      const kind = index === 0 || shown[index - 1].type !== token.type ? EVIDENCE_LABEL[token.type] ?? token.type : '';
      const value = token.text === undefined ? kind ? (token.count > 1 ? `${token.count}건` : '') : `번호 없는 ${token.count}건` : token.type === 'requirement'
        ? <Link to={`/tft/projects/${projectId}/requirements/${token.id}`}>{token.text}</Link> : token.text;
      return <Fragment key={token.id}>{index > 0 && ', '}{kind}{kind && value && ' '}{value}</Fragment>;
    })}{tokens.length > 6 && <> <button type="button" className="draft-more" aria-expanded={allEvidence} onClick={() => setAllEvidence(!allEvidence)}>{allEvidence ? '접기' : `외 ${tokens.length - 6}개`}</button></>}</p>}
    <p className="draft-status">{SOURCE_LABEL[row.provenance_kind] ?? row.provenance_kind}
      {conditions.length > 0 && <>, 확인할 것: {conditions.slice(0, 2).join(' · ')}{conditions.length > 2 && <> <button type="button" className="draft-more" aria-expanded={allConditions} onClick={() => setAllConditions(!allConditions)}>{allConditions ? '접기' : `외 ${conditions.length - 2}개`}</button></>}</>}
      {row.needs_rereview && <em className="draft-flag">재검토</em>}
      {row.evidence_missing && <em className="draft-flag">근거 없음</em>}
    </p>
    {allConditions && <ul className="draft-conditions" aria-label="나머지 확인할 것">{conditions.slice(2).map((text, index) => <li key={index}>{text}</li>)}</ul>}
  </div>;
}
type Editing = { sentence: Sentence; version: number; text: string; reason: string };

const REWRITE_REASON: Record<string, string> = {
  INPUT_BLOCKED: '내부 용어 검사', INPUT_INCOMPLETE: '값 보존 검사', INPUT_LIMIT: '절당 문장 수 초과',
  CALL_FAILED: '호출 실패', OUTPUT_TRUNCATED: '출력 잘림', STRUCTURE: '응답 형식', NUMBERING: '문장 번호',
  NUMBER_CHANGED: '숫자 변경', UNIT_CHANGED: '단위 변경', IDENTIFIER_CHANGED: '번호·모델명 변경', STATUS_MISSING: '검토 상태 누락',
  COMMITMENT: '확약 표현', FORBIDDEN_CONTENT: '쓰면 안 되는 표현', TEXT_LENGTH: '문장 길이', SUBJECT_MISSING: '장비 이름 누락', NEGATION_MISSING: '부정 표현 누락',
};

function RewriteOutcome({ summary }: { summary?: RewriteSummary }) {
  if (!summary || summary.outcome === 'PENDING') return <p role="status">작업은 끝났지만 결과 요약을 읽지 못했습니다. 최신 초안의 문장 표시를 확인하세요.</p>;
  if (summary.outcome === 'DISCARDED') return <p role="status">사람 저장이 먼저 들어와 AI 결과를 버렸습니다. 저장된 초안은 그대로입니다.</p>;
  const reasons = Object.entries(summary.failures ?? {}).map(([code, count]) => `${REWRITE_REASON[code] ?? '내용 검사'} ${count}`).join(', ');
  const kept = summary.human_sentences ? ` 사람 문장 ${summary.human_sentences}개는 바꾸지 않았습니다.` : '';
  if (summary.outcome === 'FAILED') return <p role="alert">AI로 다듬지 못했습니다. 자동 정리 문장 {summary.template_sentences}개를 그대로 둡니다. 사유: {reasons}.{kept}</p>;
  if (summary.outcome === 'PARTIAL') return <p role="status">일부만 다듬었습니다. AI 작성 {summary.model_sentences}개, 자동 정리 유지 {summary.template_sentences}개. 사유: {reasons}.{kept}</p>;
  return <p role="status">AI 작성 문장 {summary.model_sentences}개를 만들었습니다.{kept}</p>;
}

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
  const [review, setReview] = useState<ReviewRow>();
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
  const [noRegenerate, setNoRegenerate] = useState(false);
  const reviews = useQuery({ queryKey: ['rfp-draft-reviews', user.id, projectId], queryFn: ({ signal }) => getReviewVersions(projectId, signal), retry: false });
  const exports = useQuery({ queryKey: ['rfp-draft-exports', user.id, projectId], queryFn: ({ signal }) => getExports(projectId, signal), retry: false });
  const refreshHistory = () => { void cache.invalidateQueries({ queryKey: ['rfp-draft-reviews', user.id, projectId] }); void cache.invalidateQueries({ queryKey: ['rfp-draft-exports', user.id, projectId] }); };
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
  const rewriteFinished = !!rewriteJob.data && ['SUCCEEDED', 'PARTIAL'].includes(rewriteJob.data.status);
  const rewriteResult = useQuery({
    queryKey: ['rfp-draft-rewrite-result', user.id, projectId, rewriteAccepted?.job_id],
    queryFn: ({ signal }) => getRewriteSummary(projectId, rewriteAccepted!.job_id, signal),
    enabled: rewriteFinished, retry: false, staleTime: Infinity,
  });
  useEffect(() => {
    const finished = rewriteJob.data;
    if (!finished || !['SUCCEEDED', 'PARTIAL'].includes(finished.status) || refreshedRewrite.current === finished.job_id) return;
    refreshedRewrite.current = finished.job_id;
    setVersion(undefined); setVersionInput('');
    void cache.invalidateQueries({ queryKey: ['rfp-draft', user.id, projectId] });
  }, [rewriteJob.data, cache, user.id, projectId]);
  const exportDone = !!job.data && !['QUEUED', 'RUNNING'].includes(job.data.status);
  useEffect(() => { if (exportDone) void cache.invalidateQueries({ queryKey: ['rfp-draft-exports', user.id, projectId] }); }, [exportDone, cache, user.id, projectId]);
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
        const saved = result as Review; setReview({ ...saved, note: next.body.note ?? null, saved_by_name: null }); setReviewInput(String(saved.review_version));
        setMessage(`검토본 ${saved.review_version}판을 저장했습니다. 발송하거나 제안을 확정한 상태가 아닙니다.`);
      } else if (next.kind === 'rewrite') { setRewriteAccepted(result as RewriteAccepted); }
      else if (next.kind === 'regenerate') {
        const made = result as Draft;
        cache.setQueryData<Draft>(['rfp-draft', user.id, projectId, undefined], made);
        setVersion(undefined); setVersionInput('');
        setMessage(`초안 ${made.version}판을 새로 만들었습니다. 직접 고친 문장은 그대로 두었습니다.`);
      } else { setAccepted(result as Accepted); setExportReview(next.body.review_version); }
      if (next.kind === 'review' || next.kind === 'export') refreshHistory();
      setCommand(undefined);
    } catch (failure) {
      if (!alive.current) return;
      if (!(failure instanceof UncertainWrite) && !(command && failure instanceof ApiError && [401, 403].includes(failure.status))) setCommand(undefined);
      if (next.kind === 'regenerate' && unsupported(failure)) { setNoRegenerate(true); setMessage('이 서버는 아직 초안 다시 만들기를 지원하지 않습니다.'); return; }
      if (next.kind === 'regenerate' && failure instanceof ApiError && ['TFT_DRAFT_JOB_ACTIVE', 'TFT_VERSION_CONFLICT'].includes(failure.code ?? '')) {
        setMessage(failure.code === 'TFT_DRAFT_JOB_ACTIVE' ? '진행 중인 처리 작업이 끝난 뒤 다시 만드세요.' : '다른 변경이 먼저 저장됐습니다. 최신 조회 뒤 다시 만드세요.');
        return;
      }
      invalid(failure);
    } finally { inFlight.current = false; setBusy(false); }
  }
  async function download(exportId: string, name: string) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(undefined);
    try {
      const blob = await downloadExport(projectId, exportId);
      if (!alive.current) return;
      const url = URL.createObjectURL(blob); const link = document.createElement('a');
      link.href = url; link.download = name; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) { invalid(failure); }
    finally { inFlight.current = false; setBusy(false); }
  }
  const sentenceNumbers = new Map(sentences?.map((row, index) => [row.sentence_id, index + 1]));
  const numbersOf = (pick: (row: Sentence) => boolean) => (sentences ?? []).filter(pick).map(row => sentenceNumbers.get(row.sentence_id)!);
  const missingNumbers = numbersOf(row => row.evidence_missing);
  const rereviewNumbers = numbersOf(row => row.needs_rereview);
  const unresolved = project.data?.unresolved_mandatory_count;
  const todo = [missingNumbers.length > 0 && `${sentenceList(missingNumbers)} 근거 추가`, rereviewNumbers.length > 0 && `${sentenceList(rereviewNumbers)} 재검토`].filter(Boolean).join(', ')
    || (unresolved ? 'RFP 검토에서 필수 요구 처리' : '검토본 저장');
  const lastReview = review?.review_version ?? project.data?.review_version;
  const reviewText = lastReview ? `${lastReview}판` : lastReview === null ? '저장 전' : '조회값 없음';
  const rewriteRunning = rewriteJob.isFetching || (!!rewriteJob.data && ['QUEUED', 'RUNNING'].includes(rewriteJob.data.status));
  const noDraftYet = draft.error instanceof ApiError && draft.error.status === 404;
  const canRegenerate = canWrite && !blocked && !expiredJob && !busy && !command && latest && !editing && !noRegenerate && !rewriteRunning && (!!data || noDraftYet);
  const editingVisible = data?.sections.some(part => (section === 'all' || section === part.section) && part.sentences.some(row => matches(row) && row.sentence_id === editing?.sentence.sentence_id));
  const exportRows = exports.data ?? [];
  // A confirmed save remains selectable even if refreshing the history fails.
  const availableReviews = [...(reviews.isError ? [] : reviews.data ?? [])];
  if (review && !availableReviews.some(row => row.review_version === review.review_version)) availableReviews.push(review);
  availableReviews.sort((a, b) => b.review_version - a.review_version);
  const selectedReview = availableReviews.find(row => String(row.review_version) === reviewInput)
    ?? (!reviewInput ? availableReviews[0] : undefined);
  const exportBusy = job.isFetching || (!!job.data && ['QUEUED', 'RUNNING'].includes(job.data.status));
  const reviewColumns: KvColumn<ReviewRow>[] = [
    { key: 'review', header: '검토본', width: 46, className: 'c', cell: row => `${row.review_version}판` },
    { key: 'saved', header: '저장', width: 84, cell: row => [shortDateTime(row.saved_at), row.saved_by_name].filter(Boolean).join(', ') },
    { key: 'note', header: '메모', cell: row => row.note || '-' },
    { key: 'output', header: '진행', width: 60, cell: row => label(OUTPUT_LABEL, row.output_status) },
    { key: 'judgement', header: '판단', width: 64, cell: row => label(JUDGEMENT_LABEL, row.judgement_status) },
    { key: 'exports', header: '내보내기', width: 60, cell: row => [...new Set(exportRows.filter(item => item.review_version === row.review_version && item.status === 'SUCCEEDED').map(item => item.format))].join(', ') || '-' },
  ];
  const exportColumns: KvColumn<ExportRow>[] = [
    { key: 'review', header: '검토본', width: 46, className: 'c', cell: row => `${row.review_version}판` },
    { key: 'format', header: '형식', width: 50, className: 'c', cell: row => row.format },
    { key: 'status', header: '상태', width: 62, cell: row => EXPORT_LABEL[row.status] ?? row.status },
    { key: 'requested', header: '요청', width: 84, cell: row => [shortDateTime(row.requested_at), row.requested_by_name].filter(Boolean).join(', ') },
    { key: 'file', header: '파일', cell: row => row.status === 'SUCCEEDED' && row.file_name
      ? <button type="button" className="draft-more" disabled={busy || blocked} onClick={() => download(row.export_id, row.file_name!)}>{row.file_name}</button> : '-' },
  ];
  const historyFallback = (query: typeof reviews | typeof exports, empty: string) =>
    query.isPending ? <Loading /> : query.isError ? unsupported(query.error) ? '이 서버는 아직 이력 목록을 지원하지 않습니다.' : <ErrorState error={query.error} onRetry={() => query.refetch()} /> : empty;
  const editor = editing && <form className="draft-editor" aria-label="문장 편집" onSubmit={event => { event.preventDefault(); void saveEdit(); }}>
    <label><span>수정 문장</span><textarea required maxLength={4000} rows={3} value={editing.text} disabled={busy} onChange={event => setEditing({ ...editing, text: event.target.value })} /></label>
    <label><span>수정 사유</span><textarea required maxLength={500} rows={2} value={editing.reason} disabled={busy} onChange={event => setEditing({ ...editing, reason: event.target.value })} /></label>
    <p>초안 {editing.version}판 기준 · 근거·조건은 보존됩니다. 고쳐 저장한 문장만 다시 확인합니다.</p>
    <div className="draft-editor-actions"><button className="kb" type="button" disabled={busy} onClick={() => setEditing(undefined)}>편집 취소</button>{' '}
    <button className="kb primary" disabled={!writable || !editing.text.trim() || !editing.reason.trim()}>문장 저장</button></div>
  </form>;
  return <main className="rfp-draft">
    <KvTitle path="제안 지원" title="초안 및 검토 이력" code="TF0104">
      <button className="kb" disabled={!canRegenerate} title={noRegenerate ? '이 서버는 아직 지원하지 않습니다.' : undefined} onClick={() => submit({ actorId: user.id, kind: 'regenerate', key: crypto.randomUUID(), body: { version: data?.version ?? 0 } })}>최신 검토 결과로 초안 다시 만들기</button>
      <button className="kb" disabled={!writable || !data || !latest || !!editing || !!command || rewriteRunning} onClick={() => submit({ actorId: user.id, kind: 'rewrite', key: crypto.randomUUID(), body: { version: data!.version } })}>AI로 문장 다듬기</button>
      <button className="kb" aria-label="선택 문장 수정" title={selectedSentence ? `S${sentenceNumbers.get(selectedSentence.sentence_id)} 문장 수정` : '표시된 문장이 없습니다.'} disabled={!writable || !latest || !!editing || !selectedSentence} onClick={() => startEdit(selectedSentence)}>문장 수정</button>
      <Link to={`/tft/projects/${projectId}`}>프로젝트</Link>{' '}
      <button className="kb" disabled={busy || draft.isFetching} onClick={() => { setVersion(undefined); setVersionInput(''); void cache.invalidateQueries({ queryKey: ['rfp-draft', user.id, projectId] }); refreshHistory(); }}>최신 조회</button>
    </KvTitle>
    <KvNext>{data ? <>
      <b>{project.data?.external_id}</b> 초안 {data.version}판, 문장 {sentences?.length}, 검토본 {reviewText}
      {' '}<b className="sep" aria-hidden="true">|</b>{' '}다음: {todo}
      {unresolved !== undefined && <>{' '}<b className="sep" aria-hidden="true">|</b>{' '}필수 요구 미해결 {unresolved}건</>}
    </> : '초안을 불러오면 다음 할 일을 표시합니다.'}</KvNext>
    <ol className="draft-workflow" aria-label="초안 작성 순서">
      <li><b>1. 검토 결과 반영</b><span><Link to={`/tft/projects/${projectId}`}>RFP 검토</Link>에서 대응·질문을 기록한 뒤 초안을 다시 만듭니다. 모델 호출은 없습니다.</span></li>
      <li><b>2. 문장 확인</b><span>본문과 근거를 읽고 고칩니다. AI 문장 다듬기는 선택 사항이며 비용이 듭니다. 직접 고친 문장은 유지됩니다.</span></li>
      <li><b>3. 검토본 저장·내보내기</b><span>확인한 전체 초안을 검토본으로 저장하고, 저장한 검토본을 DOCX로 내보냅니다.</span></li>
    </ol>
    {data?.freshness && <div className="draft-notice" aria-label="검토 결과 반영 상태">
      <p>{data.freshness.status === 'CURRENT' ? '이 초안은 현재 검토 기록으로 만들어졌습니다.' : data.freshness.status === 'STALE' ? `초안 생성 뒤 요구 ${(data.freshness.changed_requirement_ids ?? []).length}건이 바뀌었습니다. 최신 검토 결과로 초안을 다시 만들어 반영하세요.` : '이전 초안에는 반영한 요구 판본 기록이 없습니다. 최신 검토 결과로 다시 만들면 반영 상태를 확인할 수 있습니다.'}</p>
      {(data.freshness.changed_requirement_ids ?? []).length > 0 && <details><summary>바뀐 요구 보기</summary><ul>{(data.freshness.changed_requirement_ids ?? []).map(id => <li key={id}><Link to={`/tft/projects/${projectId}/requirements/${id}`}>{data.evidence_labels?.[id] ?? '요구 상세 확인'}</Link></li>)}</ul></details>}
      {(data.freshness.preserved_human_sentence_ids ?? []).length > 0 && <p>직접 수정한 {(data.freshness.preserved_human_sentence_ids ?? []).length}문장은 유지됩니다. 최신 근거와 맞는지 따로 확인하세요.</p>}
    </div>}
    {rewriteAccepted && <div className="draft-notice" aria-label="AI 문장 다듬기 작업">
      {rewriteJob.isPending && <Loading label="AI 다듬기 상태 확인 중" />}
      {rewriteJob.isError && <ErrorState error={rewriteJob.error} onRetry={() => rewriteJob.refetch()} />}
      {!rewriteJob.isError && rewriteJob.data && <p role="status">AI 문장 다듬기: {rewriteJob.data.status === 'RUNNING' ? '작성 중' : rewriteJob.data.status === 'QUEUED' ? '대기 중' : rewriteJob.data.status === 'SUCCEEDED' ? '처리 끝남' : rewriteJob.data.status === 'PARTIAL' ? '일부 완료·최신 초안 확인' : rewriteJob.data.status === 'CANCELLED' ? '취소됨·자동 정리 문장 유지' : '실패·자동 정리 문장 유지'}</p>}
      {rewriteFinished && !rewriteResult.isPending && <RewriteOutcome summary={rewriteResult.data} />}
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
        <option value="review">재검토 문장 {needsReview ?? '-'}</option>
        <option value="evidence">근거 없는 문장 {sentences?.filter(row => row.evidence_missing).length ?? '-'}</option>
        <option value="unconfirmed">확인할 조건이 남은 문장 {sentences?.filter(row => row.unconfirmed).length ?? '-'}</option>
      </select></KvCondField>
      <KvCondField label="구역"><select aria-label="구역" value={section} disabled={!data} onChange={event => setSection(event.target.value)}><option value="all">전체</option>{Object.entries(SECTIONS).map(([key, title]) => <option key={key} value={key}>{title}</option>)}</select></KvCondField>
    </KvCondRow></KvCond>
    <div className="draft-overview">
      <KvField label="프로젝트">{[project.data?.external_id, project.data?.name].filter(Boolean).join(' ') || projectId}</KvField>
      <KvField label="초안">{data ? `${data.version}판, ${shortDateTime(data.known_at)}` : '조회 전'}</KvField>
      <KvField label="검토본">{reviewText}</KvField>
      <KvField label="근거 없는 문장"><span className={data?.evidence_missing_count ? 'draft-attention' : ''}>{data ? `${data.evidence_missing_count}문장` : '-'}</span></KvField>
    </div>
    {draft.isPending && <Loading />}
    {draft.isError && <ErrorState error={draft.error} onRetry={() => draft.refetch()} />}
    {data && <nav className="draft-section-nav" aria-label="문서 구역 이동">
      {data.sections.map(part => <button key={part.section} type="button" className="kb sm" aria-pressed={section === part.section} onClick={() => { setSection(part.section); document.getElementById('draft-document')?.scrollIntoView?.({ block: 'start' }); }}>{SECTIONS[part.section] ?? part.section}<span>{part.sentences.length}</span></button>)}
      <button type="button" className="kb sm" aria-pressed={section === 'all'} onClick={() => setSection('all')}>전체 문서</button>
    </nav>}
    <div className="draft-layout"><KvSplit columns="minmax(0, 1.75fr) minmax(340px, 1fr)">
    <div className="draft-document" id="draft-document">
    <header className="draft-document-heading"><h2>{project.data?.name ?? 'RFP 대응 제안'}</h2><p>초안 {data?.version ?? '-'}판{latest ? '' : ' (지정 판본)'} · 고객 제출 전 담당자 확인 필요</p></header>
    <div className="draft-sections" aria-label="초안 문장 목록">
    {data && visibleSentences.length === 0 && <p className="draft-empty" role="status">선택한 조건의 문장이 없습니다.</p>}
    {visibleSentences.length > 0 && displayedSections.map(part => <section className="draft-document-section" key={part.section} aria-label={SECTIONS[part.section] ?? part.section}><h3>{SECTIONS[part.section] ?? part.section}<small>{part.sentences.length}문장</small></h3>
      {!part.sentences.length && <p className="draft-empty">저장된 문장이 없습니다.</p>}
      {part.sentences.filter(matches).map(row => <article className={`draft-sentence${editing?.sentence.sentence_id === row.sentence_id ? ' is-editing' : selectedSentence?.sentence_id === row.sentence_id ? ' is-selected' : ''}`} id={`draft-sentence-${row.sentence_id}`} key={row.sentence_id} aria-label={`문장 S${sentenceNumbers.get(row.sentence_id)}`}>
        <button type="button" className="draft-sentence-number" aria-label={`S${sentenceNumbers.get(row.sentence_id)} 선택`} aria-pressed={selectedSentence?.sentence_id === row.sentence_id} onClick={() => setSelectedSentenceId(row.sentence_id)}>S{sentenceNumbers.get(row.sentence_id)}</button>
        <div className="draft-sentence-body">
          {editing?.sentence.sentence_id === row.sentence_id ? editor : <>
            <button type="button" className="draft-sentence-text" aria-label={`S${sentenceNumbers.get(row.sentence_id)} 문장 선택`} aria-pressed={selectedSentence?.sentence_id === row.sentence_id} onClick={() => setSelectedSentenceId(row.sentence_id)}>{row.text}</button>
            {selectedSentence?.sentence_id === row.sentence_id && <button className="kb sm draft-edit-button" disabled={!writable || !latest || !!editing} onClick={() => startEdit(row)}>문장 수정</button>}
          </>}
        </div>
        <SentenceProof row={row} labels={data?.evidence_labels ?? {}} projectId={projectId} />
      </article>)}
    </section>)}
    {editing && !editingVisible && <KvBox title="문장 편집" note="표시 필터 밖의 선택 문장을 편집하고 있습니다.">{editor}</KvBox>}
    </div></div><aside className="draft-actions" aria-label="검토본과 내보내기">
    <KvBox title="검토본 저장 조건">
      <dl className="draft-checks">
        <div><dt>필수 요구, 상태와 근거 또는 다음 행동</dt><dd className={unresolved ? 'draft-attention' : ''}>{unresolved === undefined ? '서버 확인' : unresolved > 0 ? <Link to={`/tft/projects/${projectId}`}>미해결 {unresolved}건 검토</Link> : '미해결 0'}</dd></div>
        {project.data?.integration_responsibility?.required && project.data.integration_responsibility.holder === 'UNRESOLVED' && <div><dt>통합 책임</dt><dd className="draft-attention">미확보, <Link to={`/tft/projects/${projectId}/supplement`}>보완 화면에서 지정</Link></dd></div>}
        <div><dt>근거 없는 문장</dt><dd className={data?.evidence_missing_count ? 'draft-attention' : ''}>{data ? missingNumbers.length ? <button type="button" className="draft-more" onClick={() => { setSection('all'); setView('evidence'); }}>{sentenceList(missingNumbers)} 확인</button> : '0문장' : '조회 전'}</dd></div>
        <div><dt>재검토 필요 문장</dt><dd className={needsReview ? 'draft-attention' : ''}>{needsReview === undefined ? '조회 전' : rereviewNumbers.length ? <button type="button" className="draft-more" onClick={() => { setSection('all'); setView('review'); }}>{sentenceList(rereviewNumbers)} 확인</button> : '0문장'}</dd></div>
        <div><dt>초안</dt><dd>{data ? `${data.version}판, ${shortDateTime(data.known_at)}` : '조회 전'}</dd></div>
        <div><dt>마지막 검토본</dt><dd>{reviewText}</dd></div>
      </dl>
      <p className="draft-help">저장할 때 필수 요구와 통합 책임을 서버가 다시 확인합니다. 고쳐 저장한 문장만 재검토가 풀립니다.</p>
      {data && <p className="draft-disclaimer" role="note">{data.disclaimer} <span>{label(DATA_ORIGIN_LABEL, data.data_origin)}</span></p>}
    </KvBox>
    <KvBox title="검토본 저장" note="전체 초안 기준">
      <label className="draft-input">검토 메모<textarea rows={2} maxLength={1000} value={note} disabled={busy || !!command} onChange={event => setNote(event.target.value)} /></label>
      <button className="kb primary" disabled={!writable || !data || !latest || !!editing} onClick={() => submit({ actorId: user.id, kind: 'review', key: crypto.randomUUID(), body: { version: data!.version, note: note || null } })}>검토본 저장</button>
      {review && <div className="draft-receipt"><KvField label="저장한 검토본">{review.review_version}판 (초안 {review.draft_version}판)</KvField><KvField label="저장 일시">{shortDateTime(review.saved_at)}</KvField><KvField label="출력 상태">{label(OUTPUT_LABEL, review.output_status)} / {label(JUDGEMENT_LABEL, review.judgement_status)}</KvField></div>}
    </KvBox>
    <KvBox title={exports.data ? `내보내기 ${exports.data.length}건` : '내보내기'} note="저장한 검토본만">
      <label className="draft-input">내보낼 검토본<select value={selectedReview ? String(selectedReview.review_version) : ''} disabled={busy || !!command || !availableReviews.length || exportBusy} onChange={event => setReviewInput(event.target.value)}>
        {!selectedReview && <option value="">{reviews.isPending ? '검토 이력 불러오는 중' : '검토본을 선택하세요'}</option>}
        {availableReviews.map(row => <option key={row.review_version} value={row.review_version}>{row.review_version}판 · {shortDateTime(row.saved_at)} · {row.note || '메모 없음'}</option>)}
      </select></label>
      {selectedReview ? <p className="draft-help">초안 {selectedReview.draft_version}판을 저장한 검토본입니다. 이후 초안 수정은 이 파일에 포함되지 않습니다.</p> : !reviews.isPending && !reviews.isError && <p className="draft-help">위에서 검토본을 저장하면 내보낼 수 있습니다.</p>}
      {reviews.isError && <p className="draft-help">검토 이력을 불러오지 못했습니다.{review && ' 방금 저장한 검토본은 내보낼 수 있습니다.'} <button type="button" className="draft-more" disabled={reviews.isFetching} onClick={() => reviews.refetch()}>검토 이력 다시 조회</button></p>}
      <div className="draft-buttons"><button className="kb primary" disabled={!writable || !selectedReview || !!editing || exportBusy} onClick={() => selectedReview && submit({ actorId: user.id, kind: 'export', key: crypto.randomUUID(), body: { review_version: selectedReview.review_version, format: 'DOCX' } })}>DOCX 내보내기</button>{' '}</div>
      {accepted && <div className="draft-receipt">
        <KvField label="요청한 검토본">{exportReview}판</KvField>
        {job.isPending && <Loading label="내보내기 상태 확인 중" />}
        {job.isError && <ErrorState error={job.error} onRetry={() => job.refetch()} />}
        {!job.isError && job.data && <p role="status">{JOB_LABEL[job.data.status]}</p>}
        {job.data && ['FAILED', 'PARTIAL', 'CANCELLED'].includes(job.data.status) && <p>파일을 다운로드할 수 없습니다. 상태를 확인한 후 새 내보내기를 요청하세요.</p>}
        <button className="kb" disabled={busy || blocked || job.isError || job.data?.status !== 'SUCCEEDED'} onClick={() => download(accepted.export_id, `rfp-${accepted.export_id}.docx`)}>DOCX 다운로드</button>
      </div>}
      <KvGrid label="내보내기" columns={exportColumns} rows={exportRows} rowKey={row => row.export_id} fallback={historyFallback(exports, '내보낸 파일이 없습니다.')} />
    </KvBox>
    <KvBox title="검토 이력" note={reviews.data ? `${reviews.data.length}건` : undefined}>
      <KvGrid label="검토 이력" columns={reviewColumns} rows={reviews.data ?? []} rowKey={row => String(row.review_version)} fallback={historyFallback(reviews, '저장한 검토본이 없습니다.')} />
      <form className="draft-version-form" onSubmit={event => { event.preventDefault(); setVersion(versionInput ? Number(versionInput) : undefined); }}>
        <label className="draft-input">조회할 초안 판본 (비우면 최신)<input type="number" min="1" step="1" value={versionInput} onChange={event => setVersionInput(event.target.value)} /></label>
        <button className="kb" disabled={busy || !!editing || !!command}>판본 조회</button>
      </form>
      {!latest && <p className="draft-help">지정 판본을 보고 있습니다. 편집과 검토본 저장은 최신 초안에서 합니다.</p>}
    </KvBox>
    </aside></KvSplit></div>
  </main>;
}
