import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { getEvaluationJob, isJobId, statusLabels } from '../api/evaluations';
import './evaluations.css';

/** 평가 작업 조회 화면을 표시한다. Args: 없음. Returns: 조회 화면. */
export default function Evaluations() {
  const [params, setParams] = useSearchParams();
  const jobId = params.get('job') || '';
  return <EvaluationLookup key={jobId} jobId={jobId} onOpen={id => setParams({ job: id })} />;
}

/** 작업별 조회 상태를 분리한다. Args: 작업 ID와 이동 함수. Returns: 조회 폼과 상태. */
function EvaluationLookup({ jobId, onOpen }: { jobId: string; onOpen: (id: string) => void }) {
  const [input, setInput] = useState(jobId);
  const valid = isJobId(jobId);
  const query = useQuery({
    queryKey: ['evaluation-job', jobId],
    queryFn: ({ signal }) => getEvaluationJob(jobId, signal),
    enabled: valid, retry: false, gcTime: 0, staleTime: 0,
  });
  const data = query.isSuccess && !query.isFetching ? query.data : undefined;
  return <main className="evaluation-page">
    <header className="evaluation-heading">
      <div><p className="eyebrow">EVALUATIONS</p><h1>평가 실행</h1><p>접수한 작업의 현재 상태를 확인하세요.</p></div>
      <span className="evaluation-tag">실행 상태 조회</span>
    </header>
    <section aria-labelledby="lookup-title">
      <h2 id="lookup-title">작업 조회</h2>
      <form className="evaluation-form" onSubmit={event => {
        event.preventDefault();
        const id = input.trim().toLowerCase();
        if (id === jobId) void query.refetch(); else onOpen(id);
      }}>
        <label htmlFor="evaluation-job-id">작업 ID<span>평가 접수 시 받은 job_id를 입력하세요.</span></label>
        <div><input id="evaluation-job-id" value={input} onChange={event => setInput(event.target.value)}
          placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" autoComplete="off" spellCheck={false} />
        <button type="submit" disabled={!isJobId(input.trim()) || query.isFetching}>조회</button></div>
      </form>
      {jobId && !valid && <p role="alert" className="error">올바른 작업 ID를 입력해 주세요.</p>}
    </section>
    <section className="evaluation-result" aria-labelledby="result-title" aria-busy={query.isFetching}>
      <div className="section-title"><h2 id="result-title">실행 현황</h2>
        {valid && <button onClick={() => void query.refetch()} disabled={query.isFetching}>새로고침</button>}
      </div>
      {!valid && <div className="evaluation-empty"><span aria-hidden="true">◎</span><h3>확인할 작업을 선택하세요</h3><p>작업 ID를 조회하면 실행 상태와 검토 필요 여부가 표시됩니다.</p></div>}
      {valid && query.isFetching && <p role="status" className="state">현재 상태를 확인하고 있습니다.</p>}
      {valid && query.isError && !query.isFetching && <div role="alert" className="state error"><h3>상태 조회를 완료하지 못했습니다</h3><p>{query.error instanceof TypeError || query.error.name === 'TimeoutError' ? '서버에 연결하지 못했습니다. 연결을 확인한 뒤 다시 조회해 주세요.' : query.error.message}</p><p>이 화면에서는 작업의 성공 여부를 확인할 수 없습니다.</p></div>}
      {data && <>
        <div className="evaluation-summary"><span className={`evaluation-status status-${data.run_status.toLowerCase()}`}>{statusLabels[data.run_status]}</span>
          <p>{data.needs_review ? '담당자 검토가 필요합니다.' : '서버에 기록된 실행 상태입니다.'}</p>
        </div>
        <dl><dt>실행 ID</dt><dd><code>{data.run_id}</code></dd><dt>작업 ID</dt><dd><code>{data.job_id}</code></dd>
          <dt>검토 필요</dt><dd>{data.needs_review ? '필요' : '없음'}</dd>
          <dt>마지막 확인</dt><dd><time dateTime={new Date(query.dataUpdatedAt).toISOString()}>{new Date(query.dataUpdatedAt).toLocaleTimeString('ko-KR')}</time></dd></dl>
        <p className="evaluation-caption">조회 시점의 상태입니다. 최신 상태는 새로고침으로 확인하세요.</p>
      </>}
    </section>
    <aside className="evaluation-note"><strong>평가 결과와 실행 상태는 별도로 확인합니다.</strong><p>현재 지원 범위는 합성 평가의 실행 상태 조회입니다. 실행 완료는 모델 품질의 합격을 의미하지 않습니다. 최종 시험과 원시 출력은 잠금 상태를 유지합니다.</p></aside>
  </main>;
}
