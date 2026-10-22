'use strict';

const START = '<!-- jira-links:start -->';
const END = '<!-- jira-links:end -->';
const KEY = /(?<![A-Za-z0-9])KAN-[1-9][0-9]*(?![A-Za-z0-9])/g;

function issueKeys(text) {
  return [...new Set((text || '').match(KEY) || [])];
}

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

function reviewersFor(keys, config, author) {
  const unknown = keys.filter(key => !config.reviewersByIssue[key]);
  const reviewers = [...new Set(keys.map(key => config.reviewersByIssue[key]).filter(Boolean))]
    .filter(login => login.toLowerCase() !== author.toLowerCase());
  return { reviewers, unknown };
}

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
      .addRaw('관련 Jira 번호와 포함 기능, 검증 결과를 PR에 작성하고 검토자 1명을 직접 지정하세요.\n')
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
  const route = reviewersFor(metadata.keys, config, pr.user.login);
  // Waivers are exact initial-setup branches, never a permanent ticket exemption.
  const waiver = (config.setupReviewWaivers || []).find(item =>
    item.branch === pr.head.ref && metadata.keys.every(key => item.keys.includes(key)));
  if (route.unknown.length) core.warning(`검토 배정표에 ${route.unknown.join(', ')}가 없습니다. Jira 검토자를 확인해 직접 지정하고 배정표를 갱신하세요.`);
  const requested = [];
  if (!pr.draft && !waiver && route.reviewers.length) {
    const reviews = await github.paginate(github.rest.pulls.listReviews, { ...params, per_page: 100 });
    const latest = new Map();
    for (const review of reviews) if (review.user && review.state !== 'PENDING') latest.set(review.user.login.toLowerCase(), review.state);
    const already = new Set((pr.requested_reviewers || []).map(user => user.login.toLowerCase()));
    for (const login of route.reviewers) {
      if (!already.has(login.toLowerCase()) && (!latest.has(login.toLowerCase()) || latest.get(login.toLowerCase()) === 'DISMISSED')) requested.push(login);
    }
    if (requested.length) await github.rest.pulls.requestReviewers({ ...params, reviewers: requested });
  }
  if (!pr.draft && !waiver && !route.reviewers.length) core.warning('자동 지정할 수 있는 다른 검토자가 없습니다. Jira의 검토 배정을 확인해 직접 지정하세요.');
  await core.summary.addHeading('Jira 연결 검사')
    .addRaw(`티켓: ${metadata.keys.join(', ')}\n\n`)
    .addRaw(waiver ? `초기 셋업 검토 생략: ${waiver.reason}\n` : pr.draft ? 'Draft PR이므로 검토 요청은 보류했습니다.\n' : `검토 배정: ${route.reviewers.join(', ') || '직접 지정 필요'}\n`)
    .addRaw('\nJira의 최종 완료 상태와 배포 승인은 변경하지 않습니다.\n')
    .write();
}

module.exports = { issueKeys, normalize, reviewersFor, run };
