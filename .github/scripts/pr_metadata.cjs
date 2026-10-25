'use strict';

const START = '<!-- jira-links:start -->';
const END = '<!-- jira-links:end -->';
const KEY = /(?<![A-Za-z0-9])KAN-[1-9][0-9]*(?![A-Za-z0-9])/g;

/** Jira 번호를 중복 없이 추출한다. Args: text — 검사할 문자열. Returns: Jira 번호 배열. */
function issueKeys(text) {
  return [...new Set((text || '').match(KEY) || [])];
}

/** PR 번호와 링크를 검사·보완한다. Args: PR 메타데이터, Jira 주소. Returns: 보완한 메타데이터. */
function normalize({ branch, title, body }, jiraBaseUrl) {
  const keys = issueKeys(branch);
  if (!keys.length) throw new Error('브랜치 이름에 실제 Jira 번호(KAN-63 등)가 필요합니다.');
  const titleKeys = issueKeys(title);
  const wrong = titleKeys.filter(key => !keys.includes(key));
  if (wrong.length) throw new Error(`PR 제목의 ${wrong.join(', ')} 번호가 브랜치와 다릅니다. 내용을 확인해 주세요.`);
  const missing = keys.filter(key => !titleKeys.includes(key));
  const newTitle = missing.length ? `${missing.map(key => `[${key}]`).join(' ')} ${title.trim()}` : title;
  const currentBody = body || '';
  const starts = currentBody.split(START).length - 1;
  const ends = currentBody.split(END).length - 1;
  if (starts !== ends || starts > 1 || (starts && currentBody.indexOf(START) > currentBody.indexOf(END))) {
    throw new Error('PR 본문의 jira-links 표시가 손상되거나 중복되었습니다. 시작 및 끝 표시를 한 쌍으로 맞춰 주세요.');
  }
  const block = `${START}\n${keys.map(key => `- Jira: [${key}](${jiraBaseUrl}/browse/${key})`).join('\n')}\n${END}`;
  const newBody = starts
    ? currentBody.slice(0, currentBody.indexOf(START)) + block + currentBody.slice(currentBody.indexOf(END) + END.length)
    : block + (currentBody ? '\n\n' + currentBody : '');
  return { keys, title: newTitle, body: newBody };
}

/** Jira 연결만 갱신한다. Args: GitHub API·이벤트·요약·설정. Returns: 완료 Promise. */
async function run({ github, context, core, config }) {
  const params = { owner: context.repo.owner, repo: context.repo.repo, pull_number: context.payload.pull_request.number };
  // Read the latest PR so delayed events do not overwrite a newer edit.
  const { data: pr } = await github.rest.pulls.get(params);
  if (pr.state !== 'open') return;
  const sameRepository = pr.head.repo?.full_name === `${params.owner}/${params.repo}`;
  const integration = sameRepository && (
    (pr.head.ref === 'dev' && pr.base?.ref === 'main') ||
    (pr.head.ref === 'main' && pr.base?.ref === 'dev'));
  if (integration) {
    await core.summary.addHeading('통합 브랜치 PR')
      .addRaw(`${pr.head.ref} → ${pr.base.ref}: Create a merge commit으로 이력을 유지하세요.\n\n`)
      .addRaw('관련 Jira와 CI·최신 SHA AI 리뷰 결과를 확인한 뒤 작성자가 병합하세요.\n')
      .addRaw('Jira 완료 상태와 배포 승인은 변경하지 않습니다.\n')
      .write();
    return;
  }
  const metadata = normalize({ branch: pr.head.ref, title: pr.title, body: pr.body }, config.jiraBaseUrl);
  if (metadata.title !== pr.title || metadata.body !== (pr.body || '')) {
    await github.rest.pulls.update({ ...params, title: metadata.title, body: metadata.body });
  }
  // Validate immediately. GITHUB_TOKEN edits do not cause another edited run.
  normalize({ branch: pr.head.ref, title: metadata.title, body: metadata.body }, config.jiraBaseUrl);
  await core.summary.addHeading('Jira 연결 검사')
    .addRaw(`티켓: ${metadata.keys.join(', ')}\n\n`)
    .addRaw('팀원을 자동 지정하지 않습니다. CI와 최신 SHA AI 리뷰 결과를 확인하세요.\n')
    .addRaw('\n이 워크플로는 Jira 상태를 직접 변경하지 않습니다. 최종 PR 제목의 [완료 KAN-번호]와 dev 병합 조건을 충족하면 별도 Jira 규칙이 해당 티켓을 완료로 전환합니다. 중간 PR에는 완료 표시를 넣지 마세요.\n')
    .write();
}

module.exports = { issueKeys, normalize, run };
