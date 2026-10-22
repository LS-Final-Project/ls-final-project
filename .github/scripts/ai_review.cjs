'use strict';

const schema = {
  type: 'object', additionalProperties: false,
  required: ['head_sha', 'base_sha', 'review_complete', 'reviewed_files', 'summary', 'findings'],
  properties: {
    head_sha: { type: 'string' }, base_sha: { type: 'string' },
    review_complete: { type: 'boolean' },
    reviewed_files: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string', minLength: 1, maxLength: 4000 },
    findings: {
      type: 'array', maxItems: 100,
      items: {
        type: 'object', additionalProperties: false,
        required: ['severity', 'path', 'line', 'description'],
        properties: {
          severity: { type: 'string', enum: ['P0', 'P1', 'P2', 'P3'] },
          path: { type: 'string' }, line: { type: 'integer', minimum: 1 },
          description: { type: 'string', minLength: 1, maxLength: 1500 },
        },
      },
    },
  },
};

/** 최신 열린 PR을 조회한다. Args: GitHub API, 이벤트. Returns: 동일 head/base의 PR. */
async function currentPR(github, context) {
  const event = context.payload.pull_request;
  const { data: pr } = await github.rest.pulls.get({ ...context.repo, pull_number: event.number });
  if (pr.state !== 'open' || pr.draft || pr.head.sha !== event.head.sha || pr.base.sha !== event.base.sha) {
    throw new Error('PR이 닫혔거나 Draft 또는 head/base가 변경됐습니다. 최신 이벤트로 다시 검토하세요.');
  }
  return pr;
}

/** 검토 범위와 출력 스키마를 고정한다. Args: API·이벤트·출력 도구. Returns: 완료 Promise. */
async function prepare({ github, context, core }) {
  const pr = await currentPR(github, context);
  const files = await github.paginate(github.rest.pulls.listFiles, {
    ...context.repo, pull_number: pr.number, per_page: 100,
  });
  if (!files.length || files.length !== pr.changed_files) {
    throw new Error('변경 파일 전체를 조회하지 못했습니다. 검토를 완료로 처리하지 않습니다.');
  }
  core.setOutput('files', JSON.stringify(files.map(file => file.filename)));
  core.setOutput('schema', JSON.stringify(schema));
}

/** 모델 결과를 검사한다. Args: 원시 JSON, 예상 SHA·파일 목록. Returns: 검증한 결과. */
function validate(raw, { head, base, files }) {
  const result = JSON.parse(raw);
  if (!result || result.review_complete !== true || result.head_sha !== head || result.base_sha !== base ||
      typeof result.summary !== 'string' || !result.summary.trim() || result.summary.length > 4000 ||
      !Array.isArray(result.reviewed_files) || !Array.isArray(result.findings) || result.findings.length > 100) {
    throw new Error('AI 검토가 미완료이거나 결과 형식·검토 SHA가 올바르지 않습니다.');
  }
  const reviewed = new Set(result.reviewed_files);
  if (reviewed.size !== files.length || result.reviewed_files.length !== files.length ||
      files.some(file => !reviewed.has(file))) {
    throw new Error('AI가 전체 변경 파일의 검토를 완료하지 못했습니다.');
  }
  for (const finding of result.findings) {
    if (!finding || !['P0', 'P1', 'P2', 'P3'].includes(finding.severity) ||
        !files.includes(finding.path) || !Number.isInteger(finding.line) || finding.line < 1 ||
        typeof finding.description !== 'string' || !finding.description.trim() || finding.description.length > 1500) {
      throw new Error('AI 지적사항의 형식이나 파일 위치가 올바르지 않습니다.');
    }
  }
  return result;
}

/** AI 설명의 멘션·HTML을 무력화한다. Args: 모델이 생성한 문자열. Returns: 댓글용 텍스트. */
function text(value) {
  return value.replaceAll('@', '@\u200b').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

/** SHA에 결속한 결과를 게시·판정한다. Args: API·이벤트·결과·파일·실행 상태. Returns: 완료 Promise. */
async function finish({ github, context, core, raw, files, conclusion }) {
  if (conclusion !== 'success') throw new Error('Claude가 실제 검토를 완료하지 못했습니다.');
  const pr = await currentPR(github, context);
  const result = validate(raw, { head: pr.head.sha, base: pr.base.sha, files: JSON.parse(files) });
  const blocking = result.findings.filter(finding => finding.severity !== 'P3');
  const details = result.findings.map(finding =>
    `- **${finding.severity}** ${text(finding.path)}:${finding.line} — ${text(finding.description)}`);
  const body = [
    '<!-- team4-ai-review -->', '## Claude AI 코드 리뷰',
    `검토 head: \`${pr.head.sha}\``, `검토 base: \`${pr.base.sha}\``,
    `결과: **${blocking.length ? '수정 필요' : '통과'}** · 차단 지적 ${blocking.length}건`,
    text(result.summary), ...details,
    `실행: https://github.com/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`,
    '이 결과는 위 SHA의 코드 리뷰이며 업무 기능 전체 완료나 배포 승인을 뜻하지 않습니다.',
  ].join('\n\n');
  const { data: comment } = await github.rest.issues.createComment({
    ...context.repo, issue_number: pr.number, body,
  });
  try {
    await currentPR(github, context);
  } catch (error) {
    await github.rest.issues.updateComment({
      ...context.repo, comment_id: comment.id,
      body: `<!-- team4-ai-review -->\n## Claude AI 검토 무효\n게시 중 PR 상태가 바뀌었습니다. 최신 이벤트로 다시 검토하세요.\n검토했던 head: ${pr.head.sha}\n검토했던 base: ${pr.base.sha}`,
    });
    throw error;
  }
  await core.summary.addHeading('Claude AI 검토 결과').addRaw(body).write();
  if (blocking.length) core.setFailed('P0~P2 지적을 수정하고 새 커밋으로 AI 리뷰를 다시 받으세요.');
}

module.exports = { schema, currentPR, prepare, validate, finish };
