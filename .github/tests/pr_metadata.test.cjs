'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { issueKeys, normalize, run } = require('../scripts/pr_metadata.cjs');
const base = 'https://lsteam4.atlassian.net';
const config = { jiraBaseUrl: base };

test('recognizes complete Jira keys, including multiple keys', () => {
  assert.deepEqual(issueKeys('KAN-55-KAN-56-development-contracts'), ['KAN-55', 'KAN-56']);
  assert.deepEqual(issueKeys('XKAN-63 KAN-63x KAN-0 kan-63'), []);
});

test('adds title and real links without replacing human text', () => {
  const body = '## 변경 이유\n한글 설명\n\n## 확인\n- 검증 결과';
  const result = normalize({ branch: 'KAN-63-backend', title: '실행 골격', body }, base);
  assert.equal(result.title, '[KAN-63] 실행 골격');
  assert.ok(result.body.endsWith(body));
  assert.ok(result.body.includes(`${base}/browse/KAN-63`));
  assert.deepEqual(normalize({ branch: 'KAN-63-backend', ...result }, base), result);
});

test('updates only the managed block when ticket changes', () => {
  const body = '설명 앞\n<!-- jira-links:start -->\nold\n<!-- jira-links:end -->\n설명 뒤';
  const result = normalize({ branch: 'KAN-63-backend', title: '작업', body }, base);
  assert.ok(result.body.startsWith('설명 앞\n'));
  assert.ok(result.body.endsWith('\n설명 뒤'));
  assert.ok(!result.body.includes('\nold\n'));
});

test('fails on mismatched title and damaged markers instead of overwriting them', () => {
  assert.throws(() => normalize({ branch: 'KAN-63-work', title: '[KAN-64] 다른 작업', body: '' }, base));
  assert.throws(() => normalize({ branch: 'KAN-63-work', title: '작업', body: '<!-- jira-links:start -->' }, base));
  assert.throws(() => normalize({ branch: 'work', title: '[KAN-63] 작업', body: '' }, base));
});

test('keeps multiple ticket keys and does not duplicate existing title references', () => {
  const result = normalize({ branch: 'KAN-55-KAN-56-contracts', title: '[KAN-55] 계약', body: '' }, base);
  assert.deepEqual(issueKeys(result.title), ['KAN-56', 'KAN-55']);
  assert.ok(result.body.includes('/browse/KAN-55'));
  assert.ok(result.body.includes('/browse/KAN-56'));
});

/** 메타데이터 검사 환경을 만든다. Args: overrides — PR 변경값. Returns: API와 호출 기록. */
function fixture(overrides = {}) {
  const pr = { state: 'open', head: { ref: 'KAN-60-automation' }, title: '협업 자동화', body: '검증 내용', user: { login: 'oseguri' }, draft: true, requested_reviewers: [], ...overrides };
  const updates = [], requests = [], warnings = [];
  const reviews = [];
  const summary = { addHeading() { return this; }, addRaw() { return this; }, async write() {} };
  const github = { rest: { pulls: {
    async get() { return { data: structuredClone(pr) }; },
    async update(value) { updates.push(value); Object.assign(pr, { title: value.title, body: value.body }); },
    async listReviews() {},
    async requestReviewers(value) { requests.push(value); pr.requested_reviewers.push(...value.reviewers.map(login => ({ login }))); },
  } }, async paginate() { return reviews; } };
  const context = { repo: { owner: 'LS-Final-Project', repo: 'ls-final-project' }, payload: { pull_request: { number: 1, title: '과거 이벤트의 제목' } } };
  return { github, context, core: { summary, warning: value => warnings.push(value) }, config, pr, updates, requests, warnings, reviews };
}

test('fetches latest PR and never requests teammates on Draft or ready PRs', async () => {
  const f = fixture();
  await run(f);
  await run(f);
  assert.equal(f.updates.length, 1);
  assert.equal(f.pr.title, '[KAN-60] 협업 자동화');
  assert.equal(f.requests.length, 0);
  f.pr.draft = false;
  await run(f);
  await run(f);
  assert.equal(f.requests.length, 0);
  assert.equal(f.warnings.length, 0);
});

test('same-repository dev/main integration keeps release text and needs no Jira branch name', async () => {
  for (const [head, target] of [['dev', 'main'], ['main', 'dev']]) {
    const f = fixture({ draft: false, head: {ref: head, repo: {full_name: 'LS-Final-Project/ls-final-project'}}, base: {ref: target}, title: '통합 버전 배포', body: '포함 작업 KAN-63과 검증 결과' });
    await run(f);
    assert.equal(f.updates.length, 0);
    assert.equal(f.requests.length, 0);
    assert.equal(f.pr.title, '통합 버전 배포');
  }
});

test('fork branches named dev and unrelated numberless branches get no integration exception', async () => {
  for (const [head, repo] of [['dev', 'fork/repository'], ['feature', 'LS-Final-Project/ls-final-project']]) {
    const f = fixture({head: {ref: head, repo: {full_name: repo}}, base: {ref: 'main'}});
    await assert.rejects(() => run(f));
    assert.equal(f.updates.length, 0);
  }
});

test('existing reviewer requests and historical approvals do not cause new requests', async () => {
  const f = fixture({ draft: false, requested_reviewers: [{ login: 'jongddugi' }] });
  f.reviews.push({ user: { login: 'jongddugi' }, state: 'APPROVED' });
  await run(f);
  assert.equal(f.requests.length, 0);
  assert.equal(f.warnings.length, 0);
});

test('does not change a mismatched PR or request review', async () => {
  const f = fixture({ title: '[KAN-63] 다른 작업', draft: false });
  await assert.rejects(() => run(f));
  assert.equal(f.updates.length, 0);
  assert.equal(f.requests.length, 0);
});

test('Jira config has no teammate assignment or setup waiver', () => {
  const file = JSON.parse(fs.readFileSync(path.join(__dirname, '../jira-config.json'), 'utf8'));
  assert.deepEqual(file, { jiraBaseUrl: base });
});

test('배포 대상 main에 새 Jira 설정이 없어도 신뢰한 기본 브랜치로 통합 검사를 실행한다', async () => {
  const workflow = fs.readFileSync(path.join(__dirname, '../workflows/pr-metadata.yml'), 'utf8');
  const ref = workflow.match(/^          ref: (.+)$/m)?.[1];
  const script = workflow.split('          script: |\n')[1]
    .split('\n').filter(line => line.startsWith('            '))
    .map(line => line.slice(12)).join('\n');
  const oldMain = fs.mkdtempSync(path.join(os.tmpdir(), 'team4-old-main-'));
  try {
    fs.mkdirSync(path.join(oldMain, '.github/scripts'), {recursive: true});
    fs.copyFileSync(path.join(__dirname, '../scripts/pr_metadata.cjs'), path.join(oldMain, '.github/scripts/pr_metadata.cjs'));
    // Actions checkout의 두 실제 선택지를 재현한다. 이전 main에는 jira-config.json이 없다.
    const checkouts = {
      '${{ github.event.repository.default_branch }}': path.resolve(__dirname, '../..'),
      '${{ github.event.pull_request.base.sha }}': oldMain,
    };
    const workspace = checkouts[ref];
    assert.ok(workspace, 'PR head 대신 신뢰한 기본 브랜치를 선택해야 한다');
    const f = fixture({draft: false, head: {ref: 'dev', repo: {full_name: 'LS-Final-Project/ls-final-project'}}, base: {ref: 'main'}});
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    await new AsyncFunction('require', 'process', 'github', 'context', 'core', script)(
      require, {env: {GITHUB_WORKSPACE: workspace}}, f.github, f.context, f.core,
    );
    assert.equal(f.updates.length, 0);
    assert.equal(f.requests.length, 0);
  } finally {
    fs.rmSync(oldMain, {recursive: true, force: true});
  }
});
