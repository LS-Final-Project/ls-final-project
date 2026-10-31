const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {test} = require('node:test');

const workflow = fs.readFileSync(path.join(__dirname, '../workflows/application-checks.yml'), 'utf8');
const childEnv = {...process.env};
delete childEnv.NODE_TEST_CONTEXT;
const infrastructure = workflow.split('  infrastructure:\n')[1].split('  evaluation:\n')[0];

test('협업 검사 명령이 실제 실패를 반환하고 최종 결과가 이를 차단한다', () => {
  const command = infrastructure.match(/name: Test PR automation\n\s+run: (.+)/)?.[1];
  assert.ok(command, '협업 검사는 infrastructure에서 실행해야 한다');
  assert.ok(!infrastructure.includes('continue-on-error:'), '검사 실패를 무시하지 않는다');
  assert.equal((infrastructure.match(/uv run --frozen python -m unittest discover -s scripts\/tests/g) || []).length, 1);
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'kan180-ci-'));
  try {
    fs.mkdirSync(path.join(directory, '.github/tests'), {recursive: true});
    fs.writeFileSync(path.join(directory, '.github/tests/failure.test.cjs'),
      "require('node:test').test('failure fixture', () => { throw new Error('expected failure'); });\n");
    const failed = spawnSync('bash', ['-e', '-c', command], {cwd: directory, env: childEnv, encoding: 'utf8'});
    assert.ifError(failed.error);
    assert.equal(failed.status, 1, failed.stderr);
    assert.match(failed.stdout + failed.stderr, /failure fixture/);

    const required = workflow.split('  required:\n')[1];
    assert.match(required, /if: always\(\)/);
    assert.match(required.match(/needs: \[(.+)\]/)[1], /\binfrastructure\b/);
    const script = required.split("<<'PYRESULT'\n")[1].split('          PYRESULT')[0]
      .split('\n').map(line => line.replace(/^          /, '')).join('\n');
    for (const result of ['failure', 'cancelled', 'success', 'skipped']) {
      const check = spawnSync('python3', ['-c', script], {
        env: {...childEnv, RESULTS: JSON.stringify({changes: {result: 'success'}, infrastructure: {result}})},
        encoding: 'utf8',
      });
      assert.ifError(check.error);
      assert.equal(check.status === 0, ['success', 'skipped'].includes(result), check.stderr);
    }
  } finally {
    fs.rmSync(directory, {recursive: true, force: true});
  }
});

test('비밀값 검사는 변경 경로와 상관없이 전체 이력을 가려서 검사하고 최종 결과를 막는다', () => {
  const secrets = workflow.split('  secrets:\n')[1]?.split('  required:\n')[0];
  assert.ok(secrets, 'secrets 작업이 있어야 한다');
  assert.ok(!/\n    needs:|\n    if:/.test(secrets), '변경 선택에 묶이지 않고 늘 돈다');
  assert.match(secrets, /fetch-depth: 0/);
  assert.match(secrets, /GITLEAKS_SHA256: [0-9a-f]{64}\n/);
  assert.match(secrets, /sha256sum -c -/);
  assert.match(secrets, /--redact/);
  assert.match(secrets, /--exit-code 1/);
  assert.match(secrets, /--log-opts="--all"/);
  assert.ok(!secrets.includes('continue-on-error:'), '검사 실패를 무시하지 않는다');
  const required = workflow.split('  required:\n')[1];
  assert.match(required.match(/needs: \[(.+)\]/)[1], /\bsecrets\b/);
  assert.match(fs.readFileSync(path.join(__dirname, '../../.gitleaks.toml'), 'utf8'), /useDefault = true/);
});

test('gitleaks 허용 규칙은 이름표 모양만 받고 실제 키 모양은 받지 않는다', () => {
  const config = fs.readFileSync(path.join(__dirname, '../../.gitleaks.toml'), 'utf8');
  const blocks = config.split('[[allowlists]]').slice(1);
  assert.ok(blocks.length > 0);
  for (const block of blocks) {
    // generic-api-key가 아닌 규칙의 허용은 한 파일의 정확한 값 하나로만 둔다.
    if (!/targetRules = \["generic-api-key"\]/.test(block)) {
      assert.match(block, /paths = \['''\^[^']+\$'''\]/, '파일 하나로 좁힌다');
      assert.match(block, /regexes = \['''\^[A-Za-z0-9]+\$'''\]/, '정확한 값 하나만 허용한다');
    }
  }
  const pattern = new RegExp(config.match(/regexes = \['''(.+?)'''\]/)[1]);
  for (const label of ['urgency_macro_f1', 'cs-comparison-key-v1', 'admission-key-123', 'fixture-server-token', 'lsv2-fixture-secret']) {
    assert.ok(pattern.test(label), label);
  }
  // 대소문자·숫자가 섞인 임의 문자열(실제 키 모양)은 허용하지 않는다. 아래 값은 모두 지어낸 것이다.
  for (const key of ['AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUvW', 'sk-proj-Ab12Cd34Ef56Gh78', 'Zx9Qw8Er7Ty6Ui5Op4', 'abc123def456ghi789',
    'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d', 'tok-9f8e7d6c5b4a', 'key_0a1b2c3d4e5f']) {
    assert.ok(!pattern.test(key), key);
  }
});
