import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { me } from '../api/platform';
import { Loading } from '../components/states';
import LsShell from '../ls-shell/LsShell';
import Account from './Account';

// /account 경로. 세션이 없으면 셸(사이드바·상단바·탭) 없이 로그인 화면만 그리고, 있으면 셸 안에 계정 화면을 그린다.
// 로그인 화면은 전체 화면용이라 셸 안에 겹치면 안 되고, 로그인 전에는 셸이 열린 화면 탭을 저장하지도 않아야 한다.
export default function AccountGate() {
  const user = useQuery({ queryKey: ['me'], queryFn: me, retry: false });
  const [signedOut, setSignedOut] = useState(false);
  if (user.isPending) return <Loading label="세션 확인 중" />;
  const account = <Account signedOutHint={signedOut} onSignedOut={() => setSignedOut(true)} />;
  if (user.isSuccess && !user.data) return account;
  return <LsShell>{account}</LsShell>;
}
