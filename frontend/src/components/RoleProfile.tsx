import headset from '../assets/ello-cs-headset.png';
import glasses from '../assets/ello-admin-glasses.png';
import wrench from '../assets/ello-dept-wrench.png';
import body from '../assets/ello.png';
import './role-profile.css';

// 역할별 엘로(KAN-240). C/S 담당자는 헤드셋, 관리자는 안경, 부서 담당자는 공구를 든다.
// 여러 역할이면 앞에서부터 고른다: 업무 홈의 주인인 C/S 담당자가 먼저다.
const ROLE_ART: [string, string, string][] = [
  ['cs_agent', 'C/S 담당자', headset],
  ['admin', '관리자', glasses],
  ['manager', '관리자', glasses],
  ['dept_staff', '부서 담당자', wrench],
];

export function roleArt(roles: readonly string[] | undefined): { label: string | null; src: string } {
  const hit = ROLE_ART.find(([role]) => roles?.includes(role));
  return hit ? { label: hit[1], src: hit[2] } : { label: null, src: body };
}

// 인사말 왼쪽의 원형 프로필. 그림은 꾸밈이라 화면 읽기에서 빼고, 역할은 배지 글자로 읽힌다.
export default function RoleProfile({ roles }: { roles: readonly string[] | undefined }) {
  const art = roleArt(roles);
  return <div className="role-pf" data-role={art.label ?? 'none'}>
    <div className="ring"><img src={art.src} alt="" /></div>
    <span className="on" title="로그인 중" aria-hidden="true" />
  </div>;
}
