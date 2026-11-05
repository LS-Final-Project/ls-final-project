import type { ReactNode } from 'react';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';

// 메일 화면의 운영 접근 확인. 본인 메일함은 모든 업무 역할이 쓰므로(권한 문서 5.2) C/S 또는 제안 업무 역할이 있으면 연다.
// 두 확인은 같은 ['me'] 조회를 함께 쓴다. 메일함 소유·발송 승인자 확인은 플랫폼이 한다.
export default function MailGate({ title, children }: { title: string; children: ReactNode }) {
  const cs = useBusinessAccess('cs');
  const tft = useBusinessAccess('tft');
  if (!cs.ready && !tft.ready) return <BusinessGate access={cs} title={title} />;
  return <>{children}</>;
}
