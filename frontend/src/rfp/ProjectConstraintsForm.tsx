import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { ApiError, getApiMode } from '../api/client';
import { updateProjectConstraints, type RfpProject } from '../api/rfp';
import { getProject } from '../api/tft';
import { KvBox, KvField } from '../ls-shell/kv';
import { OFF_LIST_PERMISSION_LABEL } from './labels';
import { Failure } from './proposal-shared';

export type ConstraintInput = { region: string; manufacturers: string; permission: 'ALLOWED' | 'FORBIDDEN' | 'UNSTATED' };
export const emptyConstraints: ConstraintInput = { region: '', manufacturers: '', permission: 'UNSTATED' };
export const constraintsBody = (input: ConstraintInput) => ({
  supply_region: input.region.trim() || null,
  customer_manufacturer_list: input.manufacturers.split('\n').map(s => s.trim()).filter(Boolean).length
    ? input.manufacturers.split('\n').map(s => s.trim()).filter(Boolean) : null,
  off_list_permission: input.permission,
});
export const validConstraints = (input: ConstraintInput) => {
  const body = constraintsBody(input);
  return (body.supply_region?.length ?? 0) <= 200 && (body.customer_manufacturer_list?.length ?? 0) <= 100
    && (body.customer_manufacturer_list ?? []).every(name => name.length <= 200);
};
const fromProject = (project: Pick<RfpProject, 'supply_region' | 'customer_manufacturer_list' | 'off_list_permission'>): ConstraintInput => ({
  region: project.supply_region ?? '', manufacturers: (project.customer_manufacturer_list ?? []).join('\n'), permission: project.off_list_permission ?? 'UNSTATED',
});

export function ConstraintFields({ value, onChange, disabled = false }: { value: ConstraintInput; onChange: (value: ConstraintInput) => void; disabled?: boolean }) {
  return <fieldset disabled={disabled} aria-label="협력사 검토 입력">
    <KvField label="공급 지역"><input aria-label="공급 지역" maxLength={200} value={value.region} onChange={e => onChange({ ...value, region: e.target.value })} /></KvField>
    <KvField label="고객 제조사 목록"><textarea aria-label="고객 제조사 목록" rows={3} value={value.manufacturers} onChange={e => onChange({ ...value, manufacturers: e.target.value })} /></KvField>
    <KvField label="목록 밖 제조사 허용"><select aria-label="목록 밖 제조사 허용" value={value.permission} onChange={e => onChange({ ...value, permission: e.target.value as ConstraintInput['permission'] })}>
      {Object.entries(OFF_LIST_PERMISSION_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
    </select></KvField>
    <p>사람이 확인한 값만 입력하세요. 제조사는 한 줄에 하나씩, 빈 입력은 미제공입니다. 목록이 있어도 목록 밖 제조사를 자동으로 금지하지 않습니다.</p>
  </fieldset>;
}

// 프로젝트/계정 전환 시 부모 key로 새로 만든다. 늦게 온 저장 결과는 이전 폼에만 속한다.
export default function ProjectConstraintsForm({ project, onClose }: { project: Pick<RfpProject, 'id' | 'version' | 'supply_region' | 'customer_manufacturer_list' | 'off_list_permission'>; onClose: () => void }) {
  const client = useQueryClient();
  const mode = getApiMode();
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const [input, setInput] = useState(() => fromProject(project));
  const [version, setVersion] = useState(project.version);
  const [stale, setStale] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<unknown>();
  const save = useMutation({
    mutationFn: () => updateProjectConstraints(project.id, version, constraintsBody(input)),
    onSuccess: () => { if (!active.current) return; client.invalidateQueries({ queryKey: ['projects', mode] }); client.invalidateQueries({ queryKey: ['rfp-supplement', mode] }); client.invalidateQueries({ queryKey: ['rfp-integration-candidates', mode, project.id] }); onClose(); },
    onError: error => { if (active.current && error instanceof ApiError && error.status === 409) setStale(true); },
  });
  const reload = async () => {
    setRefreshing(true); setRefreshError(undefined);
    try {
      const latest = (await getProject(project.id)).data;
      if (!active.current) return;
      setInput(fromProject(latest)); setVersion(latest.version); setStale(false); save.reset();
    } catch (error) { if (active.current) setRefreshError(error); }
    finally { if (active.current) setRefreshing(false); }
  };
  const ready = validConstraints(input) && !save.isPending && !stale && !refreshing;
  return <KvBox title="협력사 검토 입력 수정" note="저장 후 처리 재실행이 필요합니다. 저장된 등급이 즉시 바뀌지는 않습니다.">
    <form onSubmit={e => { e.preventDefault(); if (ready) save.mutate(); }}>
      <ConstraintFields value={input} onChange={setInput} disabled={save.isPending || refreshing} />
      {!validConstraints(input) && <p role="alert">공급 지역·제조사명은 200자 이내, 목록은 100개 이내로 입력하세요.</p>}
      {stale && <p role="alert">프로젝트 판본이 바뀌었습니다. 최신 입력을 불러온 뒤 다시 확인하여 저장하세요. 이전 요청을 자동으로 다시 보내지 않습니다.</p>}
      {stale && <button type="button" disabled={refreshing} onClick={reload}>최신 입력 불러오기</button>}
      <button type="submit" disabled={!ready}>입력 저장</button><button type="button" disabled={save.isPending} onClick={onClose}>취소</button>
      {save.isError && <Failure error={save.error} onReload={reload} messages={{ TFT_VERSION_CONFLICT: "다른 사람이 프로젝트를 변경했습니다. 최신 입력을 확인하세요." }} />}{refreshError !== undefined && <Failure error={refreshError} onReload={reload} />}
    </form>
  </KvBox>;
}
