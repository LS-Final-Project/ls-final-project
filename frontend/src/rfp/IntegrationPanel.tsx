import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { getApiMode } from '../api/client';
import { listProjectCandidates, updateIntegration } from '../api/rfp';
import { getProject } from '../api/tft';
import { KvBox, KvField } from '../ls-shell/kv';
import { INTEGRATION_HOLDER_LABEL, label, shortDateTime } from './labels';
import { useNames } from './names';
import { Failure } from './proposal-shared';

type Holder = 'OWN' | 'SINGLE_PARTNER' | 'JOINT' | 'UNRESOLVED';
const NEEDS_PARTNER = new Set<Holder>(['SINGLE_PARTNER', 'JOINT']);
const CONFLICT = { TFT_VERSION_CONFLICT: '다른 사람이 프로젝트를 먼저 바꿨습니다. 다시 불러온 뒤 저장하세요. 입력한 내용은 남습니다.' };

// 조립품 통합 책임 지정(명세 v1.3 §7). 필요하지 않은 프로젝트에는 보이지 않는다.
// 필요한데 미확보면 검토본을 저장할 수 없다. 보드에 없어 보완 화면에 둔다: 처리 순서가 보완 경로와 같은 결정이다.
export default function IntegrationPanel({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const mode = getApiMode();
  const client = useQueryClient();
  const project = useQuery({ queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId) });
  const data = project.data?.data;
  const stored = data?.integration_responsibility;
  // 생성 타입은 기본값이 있는 칸을 선택으로 낸다. 서버는 항상 준다.
  const current = stored && { ...stored, holder: stored.holder ?? 'UNRESOLVED', holder_candidate_codes: stored.holder_candidate_codes ?? [], basis_clause_nos: stored.basis_clause_nos ?? [], unresolved: stored.unresolved ?? [], fallback_paths: stored.fallback_paths ?? [] };
  const nameOf = useNames([current?.set_by]);
  const [editing, setEditing] = useState(false);
  const [holder, setHolder] = useState<Holder>('UNRESOLVED');
  const [codes, setCodes] = useState<string[]>([]);
  const [note, setNote] = useState('');
  // 장비별 후보는 유닛 공급 가능을 뜻하지 않는다. 통합 가능이고 제외되지 않은 후보만 낸다.
  const candidates = useQuery({
    queryKey: ['rfp-integration-candidates', mode, projectId],
    queryFn: () => listProjectCandidates(projectId),
    enabled: editing,
  });
  const save = useMutation({
    mutationFn: () => updateIntegration(projectId, data?.version as number, {
      holder,
      holder_candidate_codes: NEEDS_PARTNER.has(holder) ? codes : [],
      note: note.trim() || null,
    }),
    onSuccess: () => {
      setEditing(false);
      client.invalidateQueries({ queryKey: ['projects', mode] });
      client.invalidateQueries({ queryKey: ['rfp-draft-project'] });
    },
  });
  if (!data || !current?.required) return null;
  const open = () => {
    setHolder(current.holder);
    setCodes(current.holder_candidate_codes);
    setNote(current.note ?? '');
    save.reset();
    setEditing(true);
  };
  // 이미 고른 후보가 나중에 제외돼도 보이게 둔다. 안 보이면 다음 저장에 몰래 함께 나간다.
  const capable = (candidates.data?.data.items ?? []).filter(c => codes.includes(c.candidate_code) || (c.integrator_capable && c.overall_grade !== 'EXCLUDED'));
  const unresolved = current.holder === 'UNRESOLVED';
  return <div style={{ margin: '8px 14px 0' }}>
    <KvBox title="통합 책임" note={unresolved ? '미확보, 검토본 저장 막힘' : label(INTEGRATION_HOLDER_LABEL, current.holder)}>
      <KvField label="책임자">{label(INTEGRATION_HOLDER_LABEL, current.holder)}{current.holder_candidate_codes.length > 0 && `, ${current.holder_candidate_codes.join(', ')}`}</KvField>
      <KvField label="근거 조항">{current.basis_clause_nos.join(', ') || '없음'}</KvField>
      {current.unresolved.length > 0 && <KvField label="확인할 일">{current.unresolved.join(', ')}</KvField>}
      {current.fallback_paths.length > 0 && <div className="kv-field"><span>처리 순서</span><ol style={{ margin: 0, paddingLeft: 18 }}>
        {current.fallback_paths.map(path => <li key={path.holder}>{label(INTEGRATION_HOLDER_LABEL, path.holder)}: {path.condition}</li>)}
      </ol></div>}
      {current.note && <KvField label="메모">{current.note}</KvField>}
      {current.set_at && <KvField label="지정">{[nameOf(current.set_by), shortDateTime(current.set_at)].filter(Boolean).join(', ')}</KvField>}
      {!editing && canEdit && <div className="kv-actions"><button className="kb" onClick={open}>{unresolved ? '책임자 지정' : '책임자 변경'}</button></div>}
      {!canEdit && <p className="kv-sub" style={{ margin: '6px 0 0' }}>담당 또는 책임자만 지정합니다.</p>}
      {editing && <form onSubmit={event => { event.preventDefault(); save.mutate(); }}>
        <div className="kv-field"><label htmlFor="integration-holder">책임자</label>
          <select id="integration-holder" value={holder} onChange={event => setHolder(event.target.value as Holder)}>
            {Object.entries(INTEGRATION_HOLDER_LABEL).filter(([code]) => code !== 'HOLD').map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select></div>
        {NEEDS_PARTNER.has(holder) && <div className="kv-field"><span>협력사</span><div className="ro" style={{ gap: 12, flexWrap: 'wrap' }}>
          {candidates.isPending ? '읽는 중입니다.' : candidates.isError ? '후보를 읽지 못했습니다.' : capable.length === 0 ? '통합 가능으로 표시된 후보가 없습니다.' : capable.map(c => <label key={c.candidate_code}>
            <input
              type="checkbox"
              style={{ display: 'inline-block', width: 'auto', minHeight: 0, margin: '0 4px 0 0' }}
              checked={codes.includes(c.candidate_code)}
              onChange={event => setCodes(event.target.checked ? [...codes, c.candidate_code] : codes.filter(code => code !== c.candidate_code))}
            />{c.display_name ?? c.candidate_code}</label>)}
        </div></div>}
        <div className="kv-field"><label htmlFor="integration-note">메모</label>
          <input id="integration-note" type="text" maxLength={1000} value={note} onChange={event => setNote(event.target.value)} /></div>
        <div className="kv-actions">
          <button type="button" className="kb" onClick={() => setEditing(false)}>취소</button>
          <button type="submit" className="kb primary" disabled={save.isPending}>저장</button>
        </div>
        {save.isError && <Failure error={save.error} onReload={() => { save.reset(); project.refetch(); }} messages={CONFLICT} />}
      </form>}
    </KvBox>
  </div>;
}
