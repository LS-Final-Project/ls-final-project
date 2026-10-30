import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { KvNext, KvTitle } from './kv';

const STEPS = ['접수', '검토중', '부서 검토', '고객 회신 대기', '오늘 종결'];
const TERMS: [string, string][] = [
  ['내 차례', '다음 행동을 내가 해야 하는 문의. 부서나 고객 답을 기다리는 건은 빠집니다.'],
  ['경과일', '접수된 날부터 오늘까지 며칠 지났는지. 7일이 넘으면 먼저 봅니다.'],
  ['부서 검토', '기술 판단이 필요해 담당 부서에 넘긴 상태. 답변이 오면 다시 내 차례가 됩니다.'],
  ['고객 회신 대기', '고객에게 정보를 더 물어 둔 상태. 고객 답장이 오면 자동으로 돌아옵니다.'],
  ['개선요청', '단순 문의가 아니라 제품이나 절차를 고쳐 달라는 건. 번호가 IR로 시작합니다.'],
  ['반복 사례 모음', '비슷한 문의와 그때의 답변을 모아 둔 곳. 답변 초안의 근거로 씁니다.'],
];
const PENDING = '연결 전입니다. 업무 상태와 처리 기록 API가 연결되면 표시됩니다.';

// 업무 홈 CS0001. 지금 API에는 업무 상태·처리 기록·공지가 없어 숫자와 목록은 비워 두고 준비 중으로 표시한다.
export default function CsHome() {
  const access = useBusinessAccess('cs');
  const queryClient = useQueryClient();
  if (!access.ready) return <BusinessGate access={access} title="업무 홈" />;
  return <main>
    <KvTitle path="C/S 관리" title="업무 홈" code="CS0001">
      <button className="kb primary" onClick={() => queryClient.invalidateQueries()}>조회</button>
      <Link className="kb" to="/cs/tickets">문의 조회</Link>
      <button className="kb" onClick={() => window.print()}>인쇄</button>
    </KvTitle>
    <KvNext>{PENDING}</KvNext>
    <div className="kv-split" style={{ gridTemplateColumns: '1fr 1.15fr' }}>
      <section className="kv-box">
        <h2>업무 개요<small>팀 전체, 받아 온 목록을 상태별로 센 것</small></h2>
        <div className="body" style={{ padding: 0 }}>
          <div className="kv-steps" style={{ margin: 0, border: 0, borderBottom: '1px solid var(--erp-border-soft)' }}>
            {STEPS.map(step => <span key={step} style={{ height: 30, lineHeight: '30px' }}>{step} <b style={{ fontWeight: 500, fontSize: 14 }}>-</b></span>)}
          </div>
          <div style={{ padding: '8px 10px', fontSize: '12.5px', lineHeight: 1.55, color: 'var(--erp-text)' }}>
            메일과 전화로 들어온 문의가 접수되면 AI가 요약과 분류를 먼저 붙입니다. 상담원이 내용을 확인하고, 필요하면 부서에 검토를 요청하거나 고객에게 정보를 더 묻고, 답변을 보내면 종결됩니다. 아래 표에는 그중 내가 움직여야 하는 건만 모여 있습니다.
          </div>
        </div>
      </section>
      <section className="kv-box">
        <h2>용어 풀이</h2>
        <div className="body" style={{ padding: '6px 10px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 14px' }}>
          {TERMS.map(([term, text]) => <div key={term} className="kv-field" style={{ gridTemplateColumns: '84px 1fr', marginTop: 0, alignItems: 'start', padding: '3px 0', borderBottom: '1px solid var(--erp-border-soft)' }}>
            <span>{term}</span><div style={{ lineHeight: 1.45 }}>{text}</div>
          </div>)}
        </div>
      </section>
    </div>
    <div className="kv-gridhead"><span>내 차례, 경과일 내림차순</span></div>
    <div className="kv-tablewrap">
      <table className="kv-grid">
        <colgroup><col style={{ width: 30 }} /><col style={{ width: 40 }} /><col style={{ width: 64 }} /><col style={{ width: 96 }} /><col style={{ width: 220 }} /><col style={{ width: 56 }} /><col style={{ width: 100 }} /></colgroup>
        <thead><tr><th className="c"><input type="checkbox" aria-label="모두 선택" disabled /></th><th className="n">No</th><th className="c">유형</th><th>번호</th><th>다음 행동</th><th className="sorted c" aria-sort="descending">경과일<span className="ic">▼</span></th><th className="c">상태</th></tr></thead>
        <tbody><tr><td colSpan={7}>{PENDING}</td></tr></tbody>
      </table>
    </div>
    <div className="kv-split" style={{ marginTop: 12 }}>
      <div>
        <div className="kv-sub" style={{ margin: '0 0 6px' }}>최근 처리 기록, 오늘</div>
        <div className="kv-tablewrap"><table className="kv-grid">
          <colgroup><col style={{ width: 56 }} /><col style={{ width: 84 }} /><col /><col style={{ width: 64 }} /></colgroup>
          <thead><tr><th>일시</th><th>번호</th><th>내용</th><th>처리자</th></tr></thead>
          <tbody><tr><td colSpan={4}>{PENDING}</td></tr></tbody>
        </table></div>
      </div>
      <div>
        <div className="kv-sub" style={{ margin: '0 0 6px' }}>공지</div>
        <div className="kv-tablewrap"><table className="kv-grid">
          <colgroup><col style={{ width: 56 }} /><col /></colgroup>
          <thead><tr><th>날짜</th><th>내용</th></tr></thead>
          <tbody><tr><td colSpan={2}>공지 API가 아직 없습니다.</td></tr></tbody>
        </table></div>
      </div>
    </div>
  </main>;
}
