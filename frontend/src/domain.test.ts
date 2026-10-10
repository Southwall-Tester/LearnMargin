import { describe, expect, it, beforeEach } from 'vitest';
import { configError, displayProgress, loadModelPreferences, rangeError, saveModelPreferences, scopeError } from './domain';
import { localArtifact } from './api';
import type { APIConfig, DocumentSummary } from './types';

const config: APIConfig = { base_url: 'https://api.deepseek.com', model: 'deepseek-flash', api_key: 'never-persist-this', protocol: 'chat_completions', vision: true, json_mode: true, timeout_seconds: 180 };
const document: DocumentSummary = { id: 'doc1', name: '教材.pdf', kind: 'pdf', unit_label: '页', total_units: 8, warnings: [], units: [] };

beforeEach(() => localStorage.clear());

describe('study ranges', () => {
  it('accepts disjoint ranges with Chinese punctuation, rejects reversed/out-of-file ranges', () => {
    expect(rangeError('1-3，5, 8', 8)).toBeNull();
    for (const value of ['', '0', '9', '4-2', '1,,2', 'all', '1.5', '-3']) expect(rangeError(value, 8)).not.toBeNull();
  });
  it('requires at least one primary range while allowing other documents as references', () => {
    expect(scopeError([document], { mode: 'pages', ranges: {}, topics: '' })).toContain('至少选择一份主材料');
    expect(scopeError([document], { mode: 'pages', ranges: { doc1: '2-4' }, topics: '' })).toBeNull();
    expect(scopeError([document, { ...document, id: 'reference', name: '参考资料.md' }], { mode: 'pages', ranges: { doc1: '2-4' }, topics: '' })).toBeNull();
    expect(scopeError([document], { mode: 'pages', ranges: { unrelated: '1' }, topics: '' })).not.toBeNull();
    expect(scopeError([document], { mode: 'pages', ranges: { doc1: '' }, topics: '' })).toContain('教材.pdf');
    expect(scopeError([document], { mode: 'topics', ranges: {}, topics: '  ' })).not.toBeNull();
    expect(scopeError([], { mode: 'all', ranges: {}, topics: '' })).not.toBeNull();
  });
});

describe('model preferences', () => {
  it('preserves an explicit reasoning effort without persisting the key', () => {
    saveModelPreferences({ ...config, reasoning_effort: 'low' });
    expect(loadModelPreferences().reasoning_effort).toBe('low');
    expect(localStorage.getItem('learnmargin.model-preferences.v1')).not.toContain(config.api_key);
    saveModelPreferences({ ...config, reasoning_effort: null });
    expect(loadModelPreferences().reasoning_effort).toBeNull();
  });
  it('never writes an API key, and ignores injected secret fields when reading', () => {
    saveModelPreferences(config);
    expect(JSON.stringify(localStorage)).not.toContain(config.api_key);
    expect(loadModelPreferences()).toEqual({ base_url: config.base_url, model: config.model, protocol: config.protocol, vision: true, json_mode: true, timeout_seconds: 180 });
    localStorage.setItem('learnmargin.model-preferences.v1', JSON.stringify({ ...config, unrelated: 'bad' }));
    expect(loadModelPreferences()).not.toHaveProperty('api_key');
    expect(loadModelPreferences()).not.toHaveProperty('unrelated');
    expect(JSON.stringify(localStorage)).not.toContain(config.api_key);
  });
  it('handles malformed saved settings without breaking the workspace', () => {
    localStorage.setItem('learnmargin.model-preferences.v1', '{broken');
    expect(loadModelPreferences()).toEqual({});
    expect(configError({ ...config, base_url: 'https://secret@example.com' })).not.toBeNull();
    expect(configError({ ...config, base_url: 'javascript:alert(1)' })).not.toBeNull();
    expect(configError({ ...config, base_url: 'http://127.0.0.1:11434/v1' })).toBeNull();
    expect(configError({ ...config, base_url: 'http://[::1]:11434/v1' })).toBeNull();
  });
  it('does not persist credentials supplied in API URLs, including old saved preferences', () => {
    for (const base_url of ['https://example.com/v1?api_key=url-secret', 'https://example.com/#url-secret',
      'https://user:url-secret@example.com', 'http://example.com/v1']) {
      expect(configError({ ...config, base_url })).not.toBeNull();
      saveModelPreferences({ ...config, base_url });
      expect(JSON.stringify(localStorage)).not.toContain(base_url);
      localStorage.setItem('learnmargin.model-preferences.v1', JSON.stringify({ ...config, base_url }));
      expect(loadModelPreferences()).not.toHaveProperty('base_url');
      expect(JSON.stringify(localStorage)).not.toContain('url-secret');
      expect(JSON.stringify(localStorage)).not.toContain(config.api_key);
    }
  });
  it('rejects raw spellings that browser URL parsing would silently normalize', () => {
    for (const base_url of ['https://api.ex\nample/v1', 'https://api.example/v1\t', '\rhttps://api.example/v1',
      'https://api.example/v1\x7f', 'https://api.example/space here', 'https://api.example/\u00a0',
      'https://api.example\\bad/v1', 'https://%61pi.example/v1', 'https://@api.example/v1',
      'https://api.example:0/v1', 'https://api.example:99999/v1', 'https:///api.example/v1',
      'https:api.example/v1', 'https://api.example/' + 'a'.repeat(4096),
      'http://127.1/v1', 'http://0x7f000001/v1']) {
      expect(configError({ ...config, base_url })).not.toBeNull();
      saveModelPreferences({ ...config, base_url });
      expect(loadModelPreferences()).not.toHaveProperty('base_url');
    }
    expect(configError({ ...config, base_url: ' https://api.example/v1/ ' })).toBeNull();
    expect(configError({ ...config, base_url: 'http://LOCALHOST:11434/v1' })).toBeNull();
  });
});

it('only embeds local API artifacts, with finite progress values', () => {
  expect(localArtifact('/api/jobs/123/artifacts/lesson.pdf')).toContain('/api/');
  for (const url of ['https://example.com/test.pdf', '//example.com', '/api/\\evil', 'javascript:alert(1)', undefined,
    '/api/../index.html', '/api/%2e%2e/index.html', '/api/jobs/%2f%2fevil', '/api/jobs/%5csecret',
    '/api/jobs/test\t/artifacts/lesson.html', '/api/jobs/test/artifacts/lesson.html?redirect=elsewhere',
    '/api/jobs/test/artifacts/lesson.pdf#fragment']) expect(localArtifact(url)).toBeUndefined();
  expect(displayProgress(NaN)).toBe(0);
  expect(displayProgress(101)).toBe(100);
  expect(displayProgress(-1)).toBe(0);
});
