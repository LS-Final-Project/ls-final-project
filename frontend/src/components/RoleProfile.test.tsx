import { expect, test } from 'vitest';
import { roleArt } from './RoleProfile';

test('역할마다 맞는 엘로를 고르고, 여러 역할이면 C/S 담당자가 먼저다', () => {
  expect(roleArt(['cs_agent']).src).toContain('ello-cs-headset');
  expect(roleArt(['dept_staff']).src).toContain('ello-dept-wrench');
  expect(roleArt(['admin']).src).toContain('ello-admin-glasses');
  expect(roleArt(['manager', 'cs_agent']).label).toBe('C/S 담당자');
  expect(roleArt([])).toMatchObject({ label: null });
  expect(roleArt(undefined).label).toBeNull();
});
