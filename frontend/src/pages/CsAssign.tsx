import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getAssignmentAgents, type AgentSkillRow } from '../api/cs-assignments';
import { listUserNames } from '../api/platform';
import AdminAssign from '../components/AdminAssign';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { useHotkey } from '../ls-shell/kv';
import { familyName, languageName } from '../ls-shell/cs-labels';
import LogTable, { type LogColumn } from './LogTable';
import './ticket-detail-v3.css';
import './admin-v3.css';

// 업무 배정 CS0902. 관리자가 미배정 문의를 자동 배정하고, 진행 문의·개선요청의 담당을 바꾼다. 통계는 업무 통계(CS0901).
export default function CsAssign() {
  const access = useBusinessAccess('cs');
  if (!access.ready) return <BusinessGate access={access} title="업무 배정" />;
  return <AssignView />;
}

function AssignView() {
  const mode = getApiMode();
  const client = useQueryClient();
  const refresh = () => { void client.invalidateQueries({ queryKey: ['cs-admin-assign', mode] }); void client.invalidateQueries({ queryKey: ['cs-assignment-agents', mode] }); };
  useHotkey('F8', refresh);
  return <main className="td3 as3">
    <div className="title">
      <span className="path">통계/보고서 ›</span><h1>업무 배정</h1><code className="kv-code">CS0902</code>
      <div className="btns">
        <Link className="kb3" to="/cs/admin">업무 통계</Link>
        <span className="vsep" />
        <button className="kb3 pri" onClick={refresh}>조회<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>
    <div className="body">
      <AdminAssign />
      <AssignmentAgents />
    </div>
  </main>;
}

// 담당자 배정 기준(KAN-214). 자동 배정 규칙이 읽는 기준표. 읽기 전용이며 서버 밖 스크립트로만 바뀐다.
function AssignmentAgents() {
  const mode = getApiMode();
  const agents = useQuery({ queryKey: ['cs-assignment-agents', mode], queryFn: ({ signal }) => getAssignmentAgents(signal), retry: false, staleTime: 0 });
  // 모양이 다른 응답(예시 없는 모의 응답 등)은 오류로 본다.
  const rows = agents.isSuccess && Array.isArray(agents.data?.data?.items) ? agents.data.data.items : undefined;
  const ids = rows?.map(item => item.user_id) ?? [];
  // 이름은 플랫폼만 안다. 못 읽으면 ID 앞부분으로 보인다.
  const names = useQuery({ queryKey: ['user-names', mode, ids], queryFn: ({ signal }) => listUserNames(ids, signal), enabled: ids.length > 0 && mode === 'live', retry: false });
  const nameOf = (id: string) => (Array.isArray(names.data?.data?.items) ? names.data.data.items : []).find(item => item.user_id === id)?.display_name ?? `사용자 ${id.slice(0, 8)}`;
  const denied = agents.error instanceof ApiError && (agents.error.status === 403 || agents.error.status === 401);
  const columns: LogColumn<AgentSkillRow>[] = [
    { key: 'who', header: '담당자', width: 260, cell: item => <b>{nameOf(item.user_id)}</b> },
    { key: 'lang', header: '언어', cell: item => item.languages.map(languageName).join('·') },
    { key: 'family', header: '제품군', className: 't', cell: item => <span title={item.families.map(familyName).join('·')}>{item.families.map(familyName).join('·')}</span> },
    { key: 'active', header: '사용', width: 64, className: 'c', cell: item => item.active ? <span className="chip">사용</span> : <span className="chip st-new">중지</span> },
    { key: 'open', header: '진행 문의', width: 80, className: 'c', cell: item => <span className="num">{item.open_count}</span> },
    { key: 'requests', header: '진행 개선', width: 80, className: 'c', cell: item => <span className="num">{item.open_request_count ?? 0}</span> },
  ];
  return <section className="pane" aria-label="담당자 배정 기준">
    <div className="ph"><h2 style={{ margin: 0, fontSize: 'inherit' }}>담당자 배정 기준</h2><div className="r"><span className="meta">가정 자료 · 규칙 {rows ? agents.data?.data.rule_version : '-'}</span></div></div>
    {agents.isPending ? <div className="none">불러오는 중</div>
      : denied ? <div className="none">C/S 담당자와 C/S 관리자만 볼 수 있습니다.</div>
        : !rows ? <div className="none">배정 기준을 불러오지 못했습니다.</div>
          : <LogTable label="담당자 배정 기준" columns={columns} rows={rows} rowKey={item => item.user_id} fallback="등록된 기준 없음. 기준이 없으면 새 문의는 자동 배정되지 않습니다." />}
    <p className="fnote">새 문의는 언어와 제품군이 맞는 담당자 중 진행 문의가 가장 적은 사람에게, 시스템 개선 후보는 제품군 담당자 중 진행 개선요청이 가장 적은 사람에게 규칙으로 배정됩니다(AI 아님). 언어·제품군은 기획서 직원 기준표의 가정 자료입니다.</p>
  </section>;
}
