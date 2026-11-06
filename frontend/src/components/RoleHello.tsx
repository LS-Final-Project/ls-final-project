import RoleProfile, { roleArt } from './RoleProfile';
import './role-hello.css';

// 첫 화면 위쪽 인사말(KAN-243). 업무 홈(KAN-240)과 같은 모양으로 역할별 엘로·역할 배지·날짜·이름을 보인다.
// role은 이 화면의 역할이다. 여러 역할을 가진 사람도 관리자 화면에서는 관리자 엘로가 나온다.
// label은 배지 글자다(예: 부서 담당자는 "변압기 기술지원 담당자"). 없으면 역할 이름을 쓴다.
export default function RoleHello({ role, label, name, headline }: { role: string; label?: string; name: string | undefined; headline: string }) {
  const badge = label ?? roleArt([role]).label;
  const today = new Date().toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
  return <div className="role-hello">
    <RoleProfile roles={[role]} />
    <div className="who">
      {badge && <span className="role">{badge}</span>}
      <small>{today}</small>
      <h2>{name ? `${name}님, ` : ''}{headline}</h2>
    </div>
  </div>;
}
