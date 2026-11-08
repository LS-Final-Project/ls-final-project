import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import RoleHello from './RoleHello';

afterEach(cleanup);

test('화면의 역할 엘로와 배지, 이름 인사말을 보인다', () => {
  const { container } = render(<RoleHello role="manager" label="C/S 관리자" name="나영" headline="오늘 업무 현황입니다" />);
  expect(screen.getByText('C/S 관리자')).toBeTruthy();
  expect(screen.getByRole('heading', { name: '나영님, 오늘 업무 현황입니다' })).toBeTruthy();
  expect(container.querySelector('img')?.getAttribute('src')).toContain('ello-admin-glasses');
});

test('배지 글자를 주지 않으면 역할 이름, 이름을 모르면 인사만 한다', () => {
  render(<RoleHello role="dept_staff" name={undefined} headline="부서 검토할 문의를 확인하세요" />);
  expect(screen.getByText('부서 담당자')).toBeTruthy();
  expect(screen.getByRole('heading', { name: '부서 검토할 문의를 확인하세요' })).toBeTruthy();
});
