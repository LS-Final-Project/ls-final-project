import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { ATTRIBUTE_LABEL, requirementNo } from './labels';

const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf-8');

describe('공용 표시 값', () => {
  it('요구 번호에서 원시 값을 지운다', () => {
    expect(requirementNo('738dbba4:1.1.A.1:1')).toBe('1.1.A.1-1');
    expect(requirementNo('R-01')).toBe('R-01');
    expect(requirementNo('738dbba4:OCR-page-1-region-3:2')).toBe('이미지 요구 2');
    expect(requirementNo('44444444-4444-4444-8444-444444444441')).toBe('요구 번호 미기재');
    expect(requirementNo('')).toBe('요구 번호 미기재');
    expect(requirementNo('doc:2.1:1')).toBe('2.1-1');
    expect(requirementNo('doc:44444444-4444-4444-8444-444444444441:1')).toBe('요구 번호 미기재');
  });

  it('속성 라벨은 서버 어휘 전부를 덮는다(tests/tft/test_rfp_requirements.py와 같은 목록)', () => {
    expect(Object.keys(ATTRIBUTE_LABEL).sort()).toEqual(JSON.parse(read('../../../docs/openapi/tft-attribute-keys.json')));
  });
});
