import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { ATTRIBUTE_LABEL, reasonText, requirementNo } from './labels';

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

describe('reasonText', () => {
  it('근거 ID·조항 코드·영문 키를 빼고 판정 말을 문장으로 바꾼다', () => {
    expect(reasonText('이미지 전사 원문 확인 필요. 원문 OCR-DOCX-0001.7 정격전압 요구 11 kV, 자사 25.8 kV(근거 DS-VCB-25kV-1250 2.1): 값 다름.'))
      .toBe('요구 11 kV, 자사 25.8 kV. 값이 다릅니다.');
    expect(reasonText('원문 2.5 secondary_voltage 요구 480 V, 자사 415 V(근거 DS-TRF-DRY-630 2.1): 미달.'))
      .toBe('요구 480 V, 자사 415 V. 요구에 못 미칩니다.');
    expect(reasonText('자사 사양 자료에 이 항목이 없어 비교하지 않았습니다.')).toBe('자사 사양 자료에 이 항목이 없어 비교하지 않았습니다.');
  });
});
