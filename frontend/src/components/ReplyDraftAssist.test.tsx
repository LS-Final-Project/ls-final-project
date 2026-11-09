import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { DeptReplyDraft, DraftKorean } from './ReplyDraftAssist';
import { ApiError } from '../api/client';
import type { ReplyAssist } from '../api/cs-workflow';

afterEach(cleanup);
const reply = (kind: ReplyAssist['kind'], text: string, extra: Partial<ReplyAssist> = {}) => ({ data: {
  ticket_id: 't1', kind, text, dept_result_id: null, commitment_warning: false, reused: false, created_at: '2026-11-09T01:00:00Z',
  requested_model: 'gemini-3.6-flash', model_version: null, prompt_version: 'v1', provenance: 'ai_draft', ...extra,
} as ReplyAssist });
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }));

test('부서 답변 기반 초안은 보여 주기만 하고 "이 초안 쓰기"를 눌러야 회신 상자에 넣는다', async () => {
  const create = vi.fn().mockResolvedValue(reply('DEPT_DRAFT', 'Firmware 4.2.1 fixes E-204.', { dept_result_id: 'r1' }));
  const onUse = vi.fn();
  render(<DeptReplyDraft create={create} onUse={onUse} />);
  click(/부서 답변으로 AI 회신 초안/);
  expect(await screen.findByLabelText('부서 답변 기반 AI 초안')).toBeTruthy();
  expect(screen.getByText(/Firmware 4.2.1 fixes E-204./)).toBeTruthy();
  expect(onUse).not.toHaveBeenCalled();
  click('이 초안 쓰기');
  expect(onUse).toHaveBeenCalledWith('Firmware 4.2.1 fixes E-204.');
});

test('저장된 초안이 있으면 처음부터 보이고, 없으면 단추만 보인다', async () => {
  const saved = vi.fn().mockResolvedValue(reply('DEPT_DRAFT', 'Saved draft.', { reused: true, dept_result_id: 'r1' }));
  render(<DeptReplyDraft create={vi.fn()} load={saved} onUse={vi.fn()} />);
  expect(await screen.findByText(/Saved draft./)).toBeTruthy();
  expect(screen.getByText(/저장본/)).toBeTruthy();
  cleanup();
  render(<DeptReplyDraft create={vi.fn()} load={vi.fn().mockResolvedValue({ data: null })} onUse={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole('button', { name: /부서 답변으로 AI 회신 초안/ })).toBeTruthy());
});

test('회신 상자가 그 초안을 들고 있으면 한 줄로 접고 "다시 보기"로 펼친다', async () => {
  const saved = vi.fn().mockResolvedValue(reply('DEPT_DRAFT', 'Long saved draft.', { reused: true }));
  render(<DeptReplyDraft create={vi.fn()} load={saved} onUse={vi.fn()} inUse />);
  expect(await screen.findByText('부서 답변 기반 AI 초안을 회신 상자에 넣었습니다')).toBeTruthy();
  expect(screen.queryByText(/Long saved draft./)).toBeNull();
  click('다시 보기');
  expect(screen.getByText(/Long saved draft./)).toBeTruthy();
  expect(screen.queryByRole('button', { name: '이 초안 쓰기' })).toBeNull();
  click('접기');
  expect(screen.queryByText(/Long saved draft./)).toBeNull();
});

test('꺼진 배포(404)는 꺼져 있다고 안내하고 단추를 막는다', async () => {
  render(<DeptReplyDraft create={vi.fn().mockRejectedValue(new ApiError(404, 'Not found'))} onUse={vi.fn()} />);
  click(/부서 답변으로 AI 회신 초안/);
  expect(await screen.findByText('AI 회신 초안 기능이 이 배포에서 꺼져 있습니다.')).toBeTruthy();
  expect((screen.getByRole('button', { name: /부서 답변으로 AI 회신 초안/ }) as HTMLButtonElement).disabled).toBe(true);
});

// 회신 상자처럼 글을 들고 있다가 반영 결과를 받으면 바뀌는 부모.
function Box({ translate, apply, start = 'We will check E-204.' }: { translate: never; apply: never; start?: string }) {
  const [text, setText] = useState(start);
  return <><textarea aria-label="보낼 내용" value={text} onChange={event => setText(event.target.value)} />
    <DraftKorean text={text} language="영어" translate={translate} apply={apply} onAccept={setText} /></>;
}

test('번역을 고쳐 반영하면 검토 상자에 보이고, 넣기를 눌러야 회신 상자의 글이 바뀐다', async () => {
  const translate = vi.fn().mockResolvedValue(reply('TO_KO', 'E-204를 확인하겠습니다.'));
  const apply = vi.fn().mockResolvedValue(reply('FROM_KO', 'We will check E-204 within 3 days.'));
  render(<Box translate={translate as never} apply={apply as never} />);
  click('한국어로 보기');
  expect(await screen.findByText('E-204를 확인하겠습니다.')).toBeTruthy();
  expect(translate).toHaveBeenCalledWith('We will check E-204.', expect.anything());
  click('한국어로 고쳐 반영');
  const reflect = () => screen.getByRole('button', { name: '영어로 반영' }) as HTMLButtonElement;
  expect(reflect().disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('한국어 수정'), { target: { value: 'E-204를 3일 안에 확인하겠습니다.' } });
  click('영어로 반영');
  expect(await screen.findByLabelText('반영 결과')).toBeTruthy();
  expect(apply).toHaveBeenCalledWith('We will check E-204.', 'E-204를 3일 안에 확인하겠습니다.', expect.anything());
  expect((screen.getByLabelText('보낼 내용') as HTMLTextAreaElement).value).toBe('We will check E-204.');
  click('회신 상자에 넣기');
  expect((screen.getByLabelText('보낼 내용') as HTMLTextAreaElement).value).toBe('We will check E-204 within 3 days.');
  // 넣으면 번역 칸을 닫고 오래된 번역 경고를 보이지 않는다. 다시 열면 지금 글로 다시 번역한다.
  expect(screen.queryByLabelText('초안 한국어 번역')).toBeNull();
  expect(screen.queryByText(/번역 뒤에 바뀌었습니다/)).toBeNull();
  click('한국어로 보기');
  await waitFor(() => expect(translate).toHaveBeenLastCalledWith('We will check E-204 within 3 days.', expect.anything()));
});

test('회신 상자의 글이 바뀌면 번역을 오래된 것으로 표시하고 다시 번역할 수 있다', async () => {
  const translate = vi.fn().mockResolvedValueOnce(reply('TO_KO', '확인하겠습니다.')).mockResolvedValueOnce(reply('TO_KO', '내일 확인하겠습니다.'));
  render(<Box translate={translate as never} apply={vi.fn() as never} />);
  click('한국어로 보기');
  await screen.findByText('확인하겠습니다.');
  fireEvent.change(screen.getByLabelText('보낼 내용'), { target: { value: 'We will check it tomorrow.' } });
  expect(screen.queryByRole('button', { name: '한국어로 고쳐 반영' })).toBeNull();
  click('다시 번역');
  await screen.findByText('내일 확인하겠습니다.');
  expect(translate).toHaveBeenLastCalledWith('We will check it tomorrow.', expect.anything());
});

test('담당자가 쓴 약속이 반영 결과에 있으면 경고하고, 실패는 이유를 보인다', async () => {
  const translate = vi.fn().mockResolvedValue(reply('TO_KO', '확인하겠습니다.'));
  const apply = vi.fn().mockRejectedValueOnce(new ApiError(502, 'failed', undefined, 'CS_REPLY_ASSIST_FAILED'))
    .mockResolvedValueOnce(reply('FROM_KO', 'We will replace it free of charge.', { commitment_warning: true }));
  render(<Box translate={translate as never} apply={apply as never} />);
  click('한국어로 보기');
  await screen.findByText('확인하겠습니다.');
  click('한국어로 고쳐 반영');
  fireEvent.change(screen.getByLabelText('한국어 수정'), { target: { value: '무상으로 교체해 드리겠습니다.' } });
  click('영어로 반영');
  expect(await screen.findByText(/AI가 글을 만들지 못했습니다/)).toBeTruthy();
  click('영어로 반영');
  await waitFor(() => expect(screen.getByText(/승인·확정 약속 문구가 있습니다/)).toBeTruthy());
});

test('이미 한국어인 초안은 번역하지 않는다고 안내한다', async () => {
  const translate = vi.fn().mockRejectedValue(new ApiError(422, 'already', undefined, 'CS_REPLY_TRANSLATION_NOT_NEEDED'));
  render(<Box translate={translate as never} apply={vi.fn() as never} start="확인 후 회신드리겠습니다." />);
  click('한국어로 보기');
  expect(await screen.findByText('초안이 이미 한국어라 번역하지 않습니다.')).toBeTruthy();
});
