import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import {
  createProject, startProcess, uploadDocument, UPLOAD_LIMIT,
  type DocumentVersion, type JobStatus,
} from '../api/rfp';
import { ErrorState } from '../components/states';
import { KvBox, KvField } from '../ls-shell/kv';
import { DATA_ORIGIN_LABEL, DEPARTMENT_LABEL, DOCUMENT_KIND_LABEL } from './labels';
import { ConstraintFields, constraintsBody, emptyConstraints, validConstraints } from './ProjectConstraintsForm';
import { settled, useAttemptKey } from './proposal-shared';

const Wrap = ({ children }: { children: ReactNode }) => <div style={{ margin: '8px 14px 0' }}>{children}</div>;

function Select({ id, label, value, onChange, options }: { id: string; label: string; value: string; onChange: (value: string) => void; options: Record<string, string> }) {
  return <div className="kv-field"><label htmlFor={id}>{label}</label>
    <select id={id} value={value} onChange={event => onChange(event.target.value)}>
      {Object.entries(options).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
    </select></div>;
}

// 프로젝트 접수(TF_K_Intake_New). 문서는 접수 뒤 문서 올리기로 넣는다.
export function ProjectForm({ onCancel }: { onCancel: () => void }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const [name, setName] = useState('');
  const [clientName, setClientName] = useState('');
  const [code, setCode] = useState('');
  const [goal, setGoal] = useState('');
  const [departments, setDepartments] = useState<string[]>([]);
  const [origin, setOrigin] = useState('synthetic');
  const [constraints, setConstraints] = useState(emptyConstraints);
  const body = {
    ...constraintsBody(constraints),
    name: name.trim(), company_code: code.trim(), client_name: clientName.trim() || null, customer_goal: goal.trim() || null,
    review_departments: departments, data_origin: origin as 'synthetic' | 'demo' | 'real', purpose: 'INITIAL_PROPOSAL' as const,
  };
  const [key, nextKey] = useAttemptKey(JSON.stringify(body));
  const create = useMutation({
    mutationFn: () => createProject(body, key),
    onSuccess: result => { if (!active.current) return; nextKey(); client.invalidateQueries({ queryKey: ['projects'] }); navigate(`/tft/projects/${result.data.id}`); },
    onError: error => { if (settled(error)) nextKey(); },
  });
  const ready = body.name !== '' && body.company_code !== '' && validConstraints(constraints);
  return <Wrap><KvBox title="프로젝트 접수" note="담당은 접수한 사람, 책임자는 관리자가 지정. 문서는 접수 뒤 문서 올리기로">
    <form onSubmit={event => { event.preventDefault(); if (ready) create.mutate(); }}>
      <div className="kv-field"><label htmlFor="project-name" className="req">프로젝트명</label><input id="project-name" type="text" value={name} onChange={event => setName(event.target.value)} /></div>
      <div className="kv-field"><label htmlFor="project-client">고객명</label><input id="project-client" type="text" value={clientName} onChange={event => setClientName(event.target.value)} /></div>
      <div className="kv-field"><label htmlFor="project-code" className="req">고객 코드</label><input id="project-code" type="text" maxLength={40} value={code} onChange={event => setCode(event.target.value)} /></div>
      <KvField label="제출 목적">초기 제안</KvField>
      <div className="kv-field"><label htmlFor="project-goal">고객 목표</label><textarea id="project-goal" rows={2} value={goal} onChange={event => setGoal(event.target.value)} /></div>
      <div className="kv-field"><span>검토 부서</span><div className="ro" style={{ gap: 12, flexWrap: 'wrap' }}>
        {Object.entries(DEPARTMENT_LABEL).map(([department, name]) => <label key={department}><input type="checkbox" style={{ display: 'inline-block', width: 'auto', minHeight: 0, margin: '0 4px 0 0' }} checked={departments.includes(department)} onChange={event => setDepartments(event.target.checked ? [...departments, department] : departments.filter(d => d !== department))} />{name}</label>)}
      </div></div>
      <ConstraintFields value={constraints} onChange={setConstraints} disabled={create.isPending} />
      <Select id="project-origin" label="자료 구분" value={origin} onChange={setOrigin} options={DATA_ORIGIN_LABEL} />
      <div className="kv-actions">
        <button type="button" className="kb" onClick={onCancel}>취소</button>
        <button type="submit" className="kb primary" disabled={!ready || create.isPending}>접수</button>
      </div>
      {create.isError && <ErrorState error={create.error} />}
    </form>
  </KvBox></Wrap>;
}

const ALLOWED = /\.(docx|pdf)$/i;
const megabytes = (size: number) => `${(size / 1024 / 1024).toFixed(1)} MB`;
export function fileProblem(file: File | null) {
  if (!file) return null;
  const problems = [];
  if (!ALLOWED.test(file.name)) problems.push('허용 형식은 DOCX, PDF입니다.');
  if (file.size > UPLOAD_LIMIT) problems.push(`${megabytes(file.size)}로 25 MB를 넘었습니다.`);
  return problems.length ? `${problems.join(' ')} 파일을 바꾸면 올리기가 다시 열립니다.` : null;
}

// 문서 올리기(TF_K_Intake_Upload). 올리기만으로는 분석하지 않는다.
export function UploadForm({ projectId, documents, onCancel, onDone }: {
  projectId: string;
  documents: DocumentVersion[];
  onCancel: () => void;
  onDone: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState('RFP_SPEC');
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [origin, setOrigin] = useState('synthetic');
  const [constraints, setConstraints] = useState(emptyConstraints);
  const [base, setBase] = useState('');
  const problem = fileProblem(file);
  const payload = [file?.name, file?.size, file?.lastModified, kind, title.trim(), note.trim(), origin, base].join('|');
  const [key, nextKey] = useAttemptKey(payload);
  const upload = useMutation({
    mutationFn: () => uploadDocument(projectId, {
      file: file as File, kind, title: title.trim(), note: note.trim(), data_origin: origin, supersedes_document_id: base || undefined,
    }, key),
    onSuccess: () => { nextKey(); onDone(); },
    onError: error => { if (settled(error)) nextKey(); },
  });
  const ready = !!file && !problem && title.trim() !== '';
  const latest = latestVersions(documents);
  return <Wrap><KvBox title="문서 올리기">
    <form onSubmit={event => { event.preventDefault(); if (ready) upload.mutate(); }}>
      <div className="kv-field"><label htmlFor="upload-file" className="req">파일</label>
        <input id="upload-file" type="file" accept=".docx,.pdf" onChange={event => {
          const next = event.target.files?.[0] ?? null;
          setFile(next);
          if (next && !title) setTitle(next.name.replace(/\.[^.]+$/, ''));
        }} /></div>
      {problem && <p role="alert" style={{ margin: '6px 0 0', color: 'var(--ls-red)' }}>{problem}</p>}
      <Select id="upload-kind" label="종류" value={kind} onChange={setKind} options={DOCUMENT_KIND_LABEL} />
      <div className="kv-field"><label htmlFor="upload-title" className="req">제목</label><input id="upload-title" type="text" maxLength={300} value={title} onChange={event => setTitle(event.target.value)} /></div>
      <Select id="upload-base" label="새 판본" value={base} onChange={setBase} options={{ '': '아니오, 새 문서', ...Object.fromEntries(latest.map(d => [d.document_id, `${d.title} (v${d.version})의 새 판본`])) }} />
      <div className="kv-field"><label htmlFor="upload-note">메모</label><input id="upload-note" type="text" maxLength={2000} value={note} onChange={event => setNote(event.target.value)} /></div>
      <Select id="upload-origin" label="자료 구분" value={origin} onChange={setOrigin} options={DATA_ORIGIN_LABEL} />
      <p className="kv-sub" style={{ margin: '6px 0 0' }}>DOCX, PDF 25 MB까지. PDF는 편집 흔적을 확인할 수 없습니다. 올리기만으로는 분석하지 않고 처리 실행에서 시작</p>
      <div className="kv-actions">
        <button type="button" className="kb" onClick={onCancel}>취소</button>
        <button type="submit" className="kb primary" disabled={!ready || upload.isPending}>올리기</button>
      </div>
      {upload.isError && <UploadFailure error={upload.error} />}
    </form>
  </KvBox></Wrap>;
}

function UploadFailure({ error }: { error: unknown }) {
  const code = error instanceof ApiError ? error.code : undefined;
  if (code === 'TFT_DOCUMENT_TOO_LARGE') return <p role="alert">파일이 25 MB를 넘었습니다.</p>;
  if (code === 'TFT_DOCUMENT_UNAVAILABLE') return <p role="alert">문서 저장소를 쓸 수 없습니다. 입력은 그대로입니다. 잠시 뒤 다시 올리세요.</p>;
  return <ErrorState error={error} />;
}

// 문서마다 최신 판본 하나. 처리 실행은 이 묶음을 보낸다.
export function latestVersions(documents: DocumentVersion[]) {
  const best = new Map<string, DocumentVersion>();
  for (const item of documents) {
    const seen = best.get(item.document_id);
    if (!seen || item.version > seen.version) best.set(item.document_id, item);
  }
  return [...best.values()];
}

const RUN_FAILURE: Record<string, string> = {
  TFT_JOB_IN_PROGRESS: '이 프로젝트에서 작업이 진행 중입니다. 프로젝트당 작업 하나라 끝나야 다시 실행할 수 있습니다.',
  TFT_PROCESSOR_NOT_CONNECTED: '처리기가 연결되어 있지 않습니다. 입력은 그대로이고 작업은 만들어지지 않았습니다. 관리자가 연결을 확인한 뒤 같은 내용으로 다시 실행하세요.',
  JOB_ADMISSION_UNAVAILABLE: '지금은 작업을 접수할 수 없습니다. 입력은 그대로이고 작업은 만들어지지 않았습니다. 잠시 뒤 다시 실행하세요.',
  TFT_REASON_REQUIRED: '이미 요구가 있어 재실행 사유가 필요합니다.',
};

// 처리 실행(TF_K_Intake_Run). 요구가 이미 있으면 사유가 필수다.
export function ProcessForm({ projectId, documents, rerun, onCancel, onStarted }: {
  projectId: string;
  documents: DocumentVersion[];
  rerun: boolean;
  onCancel: () => void;
  onStarted: (job: Pick<JobStatus, 'job_id'>) => void;
}) {
  const mode = getApiMode();
  const client = useQueryClient();
  const [reason, setReason] = useState('');
  const latest = latestVersions(documents);
  const body = { document_version_ids: latest.map(d => d.document_version_id), reason: reason.trim() || null };
  const [key, nextKey] = useAttemptKey(JSON.stringify(body));
  const start = useMutation({
    mutationFn: () => startProcess(projectId, body, key),
    onSuccess: result => { nextKey(); client.invalidateQueries({ queryKey: ['projects', mode] }); onStarted(result.data); },
    onError: error => { if (settled(error)) nextKey(); },
  });
  const ready = latest.length > 0 && (!rerun || reason.trim() !== '');
  const code = start.error instanceof ApiError ? start.error.code : undefined;
  return <Wrap><KvBox title="처리 실행">
    <KvField label="문서">{latest.map(d => `${d.title} v${d.version}`).join(', ') || '문서를 먼저 올리세요'}</KvField>
    <KvField label="단계">문서 읽기, 요구 추출, 자사 자료 검색, 비교, 보완 경로, 협력사 후보, 초안. 7단계 전부</KvField>
    {rerun && <>
      <div className="kv-field"><label htmlFor="process-reason" className="req">재실행 사유</label>
        <input id="process-reason" type="text" maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></div>
      <KvField label="덮어쓰기">손댄 요구는 덮어쓰지 않고 변경 제안으로 들어옵니다. 승인된 판단도 유지</KvField>
    </>}
    <p className="kv-sub" style={{ margin: '6px 0 0' }}>2번째 실행부터 사유 필수. 실행 중에는 문서 올리기와 다른 실행이 막힙니다</p>
    <div className="kv-actions">
      <button className="kb" onClick={onCancel}>취소</button>
      <button className="kb primary" disabled={!ready || start.isPending} onClick={() => start.mutate()}>처리 실행</button>
    </div>
    {start.isError && (code && RUN_FAILURE[code] ? <p role="alert">{RUN_FAILURE[code]}</p> : <ErrorState error={start.error} />)}
  </KvBox></Wrap>;
}
