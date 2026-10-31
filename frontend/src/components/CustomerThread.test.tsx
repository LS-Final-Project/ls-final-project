// @vitest-environment jsdom
import { afterEach, expect, test } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import CustomerThread, { latestUnansweredReply } from './CustomerThread';
import type { WorkflowRecord } from '../api/cs-workflow';

afterEach(cleanup);
const original = { subject: 'HMI touch not responding', body: 'The whole line is stopped.', received_at: '2026-10-28T00:02:00Z', error_code_reported: null, fw_version_reported: '1.12' };
const send = (over: Record<string, unknown> = {}) => ({ id: 's1', kind: 'INFO_REQUEST', body: 'Could you send a clear photo of the label?', approved_at: '2026-10-28T00:40:00Z', accepted_at: '2026-10-28T00:41:00Z', approved_by: '11111111-1111-4111-8111-111111111111', send_status: 'ACCEPTED', send_error_code: null, ...over });
const reply = (over: Record<string, unknown> = {}) => ({ message_id: 'm1', method: 'HEADER', received_at: '2026-10-29T08:40:00Z', created_at: '2026-10-29T08:41:00Z', reopened: true, ...over });
const flow = (sends: unknown[], replies: unknown[]) => ({ customer_sends: sends, customer_replies: replies }) as unknown as WorkflowRecord;

test('고객 원문·발송·회신을 최신이 위로 보이고 회신 본문은 저장하지 않았다고 밝힌다', () => {
  render(<CustomerThread original={original} workflow={flow([send()], [reply()])} />);
  const heads = [...document.querySelectorAll('.hd b')].map(node => node.textContent);
  expect(heads).toEqual(['고객 회신', '정보 요청 발송', '고객 원문']);
  expect(screen.getByText(/회신 본문은 메일 계정에서 확인합니다/)).toBeTruthy();
  expect(screen.getByText('검토중으로 복귀')).toBeTruthy();
  expect(screen.getByText('Could you send a clear photo of the label?')).toBeTruthy();
  expect(screen.getByText('The whole line is stopped.').className).toContain('kv-text');
  expect(screen.getByText('보고된 FW 1.12')).toBeTruthy();
  expect(document.querySelector('.msg.new .hd b')?.textContent).toBe('고객 회신');
});

test('우리 마지막 발송보다 앞선 회신은 새 회신으로 표시하지 않고, 흐름 정보가 없으면 원문만 보인다', () => {
  render(<CustomerThread original={original} workflow={flow([send({ accepted_at: '2026-10-30T00:00:00Z' })], [reply()])} />);
  expect(document.querySelector('.msg.new')).toBeNull();
  cleanup();
  render(<CustomerThread original={original} workflow={undefined} />);
  expect([...document.querySelectorAll('.hd b')].map(node => node.textContent)).toEqual(['고객 원문']);
});

test('발송 실패 코드와 알 수 없는 종류는 그대로 보인다', () => {
  render(<CustomerThread original={original} workflow={flow([send({ kind: 'OTHER_KIND', send_status: 'FAILED', send_error_code: 'RECIPIENT_REJECTED' })], [])} />);
  expect(screen.getByText('OTHER_KIND')).toBeTruthy();
  expect(screen.getByText('실패')).toBeTruthy();
  expect(screen.getByText('RECIPIENT_REJECTED')).toBeTruthy();
});

test('가장 늦은 미응답 회신만 골라 주고 회신이 없거나 이미 답했으면 없다', () => {
  expect(latestUnansweredReply(flow([], [reply({ message_id: 'a', received_at: '2026-10-29T01:00:00Z' }), reply({ message_id: 'b', received_at: '2026-10-29T09:00:00Z' })]))?.message_id).toBe('b');
  expect(latestUnansweredReply(flow([send({ accepted_at: '2026-10-30T00:00:00Z' })], [reply()]))).toBeUndefined();
  expect(latestUnansweredReply(flow([], []))).toBeUndefined();
  expect(latestUnansweredReply(undefined)).toBeUndefined();
});
