import test from 'node:test';
import assert from 'node:assert/strict';
import { characters, utf8Bytes, validCdkIssue, validDisplayName, validImport, validMemoryText, validPassword, validUsername } from '../src/validation.ts';

test('account names match ASCII server policy and reject hyphens', () => {
  assert.equal(validUsername('abc_123'), true);
  for (const name of ['ab', 'a-b', '中文名字', 'a'.repeat(33), 'abc\n']) assert.equal(validUsername(name), false);
});
test('password validation admits existing ten-character accounts and enforces UTF-8, not UTF-16', () => {
  assert.equal(validPassword('1234567890'), true);
  assert.equal(validPassword('a'.repeat(128)), true);
  assert.equal(validPassword('中'.repeat(85)), true);
  assert.equal(validPassword('中'.repeat(86)), false);
  assert.equal(validPassword('🔒'.repeat(64)), true);
  assert.equal(characters('🔒'.repeat(64)), 64);
  assert.equal(utf8Bytes('🔒'.repeat(64)), 256);
  for (const password of ['123456789', 'a'.repeat(129), '\ud800'.repeat(10)]) assert.equal(validPassword(password), false);
});
test('display names enforce characters, trimmed byte limit and Unicode control categories', () => {
  assert.equal(validDisplayName(' 中 '.repeat(26)), true);
  assert.equal(validDisplayName('中'.repeat(80)), true);
  assert.equal(validDisplayName('🔒'.repeat(60)), true);
  for (const name of ['', '   ', 'a'.repeat(81), '🔒'.repeat(61), 'abc\n', 'abc\u200b', '\ud800']) assert.equal(validDisplayName(name), false);
});
test('history input limit is one MiB of UTF-8 bytes and never two MiB', () => {
  assert.equal(validImport('a'.repeat(1024 * 1024)), true);
  assert.equal(validImport('a'.repeat(1024 * 1024 + 1)), false);
  assert.equal(validImport('中'.repeat(349525)), true);
  assert.equal(validImport('中'.repeat(349526)), false);
  assert.equal(validImport('  '), false);
});
test('memory forms use Unicode codepoints and preserve literal data for server prompt policy', () => {
  assert.equal(validMemoryText('🧠'.repeat(100), 100), true);
  assert.equal(validMemoryText('🧠'.repeat(101), 100), false);
  assert.equal(validMemoryText('a\u0000b', 100), true);
  assert.equal(validMemoryText('  ', 200, false), true);
  for (const content of ['  ', '\ud800']) assert.equal(validMemoryText(content, 2000), false);
  assert.equal(validMemoryText('a'.repeat(20000), 20000), true);
  assert.equal(validMemoryText('a'.repeat(20001), 20000), false);
});
test('CDK issue integer bounds mirror one million energy and fifty codes', () => {
  assert.equal(validCdkIssue(1000000, 50, 365), true);
  for (const values of [[1000001, 1, 1], [1, 51, 1], [1, 1, 366], [0, 1, 1], [1, 0, 1], [1.5, 1, 1], [1, 1, 0], [NaN, 1, 1]]) assert.equal(validCdkIssue(...values as [number, number, number]), false);
});
