import test from 'node:test';
import assert from 'node:assert/strict';
import { API_PROVIDERS, CHAT_PROVIDERS, DEFAULT_ACCESS_PROVIDER, draftFor, connectionFor, providerFor, sourceLabel, draftError } from '../src/lib/ai-access.ts';

test('Research API choices retain their endpoint and model through submission', () => {
  assert.deepEqual(API_PROVIDERS.map(p=>p.id), ['deepseek','mimo','glm','kimi','qwen','openai','silicon','minimax','openrouter','groq','together','new-api','openai-compat','api-compatible']);
  for (const preset of API_PROVIDERS.filter(p=>p.models.length)) {
    const draft=draftFor(preset.id);
    const base=draft.baseURL.replace('{WorkspaceId}','test-workspace');
    const connection=connectionFor({...draft,baseURL:base,apiKey:'TEST_ONLY_KEY'});
    assert.equal(connection.baseURL,base);
    assert.equal(connection.model,preset.models[0].id);
    assert.equal(connection.apiKey,'TEST_ONLY_KEY');
    assert.equal(connection.provider, ['openai','mimo',...CHAT_PROVIDERS].includes(preset.id)?preset.id:'api-compatible');
    assert.equal(providerFor(connection),preset.id);
    assert.equal(sourceLabel(connection),preset.name+' API');
  }
});

test('the local gateway default arrives ready to test and never leaks to another source', () => {
  const draft=draftFor(DEFAULT_ACCESS_PROVIDER);
  assert.equal(draftError(draft),'',JSON.stringify(draft));
  assert.equal(connectionFor(draft).provider,'new-api');
  // 换到别的来源时，本机默认密钥不能被顺带过去。
  assert.equal(draftFor('deepseek',draft).apiKey,'');
  assert.equal(draftFor('openai-compat',draft).apiKey,'');
});

test('chat-compatible sources keep their chosen identity through save and reload', () => {
  assert.deepEqual(CHAT_PROVIDERS, ['new-api','openai-compat']);
  for (const id of CHAT_PROVIDERS) {
    const draft={...draftFor(id),model:'glm-4.7',baseURL:'https://gateway.example.com/v1',apiKey:'TEST_ONLY_KEY'};
    assert.equal(draftError(draft),'');
    const connection=connectionFor(draft);
    assert.equal(connection.provider,id);
    assert.equal(providerFor(connection),id);
    assert.equal(sourceLabel(connection),API_PROVIDERS.find(p=>p.id===id)!.name+' API');
  }
});

test('plain http is limited to this machine and private network ranges', () => {
  const base={...draftFor('new-api'),model:'deepseek-chat',apiKey:'TEST_ONLY_KEY'};
  for (const baseURL of ['http://127.0.0.1:3000/v1','http://localhost:3000/v1','http://192.168.176.68:3000/v1',
                         'http://10.1.2.3:3000/v1','http://172.20.1.9/v1','https://new-api.example.com/v1']) {
    assert.equal(draftError({...base,baseURL}),'',baseURL);
    assert.equal(connectionFor({...base,baseURL}).baseURL,baseURL.replace(/\/+$/,''));
  }
  for (const baseURL of ['http://8.8.8.8:3000/v1','http://ollama.example.com:3000/v1','http://192.168.1.256:3000/v1',
                         'https://127.0.0.1:3000/v1?key=x','http://user:pass@127.0.0.1:3000/v1']) {
    assert.match(draftError({...base,baseURL}),/明文 http|HTTPS/,baseURL);
  }
  // The same address stays rejected for Responses-only sources.
  assert.ok(draftError({...base,provider:'api-compatible',baseURL:'http://192.168.176.68:3000/v1'}));
});

test('switching source never carries another providers key or endpoint', () => {
  const saved={provider:'mimo',model:'my-model',baseURL:'https://token-plan-cn.xiaomimimo.com/v1',apiKey:'PRIVATE_TEST_KEY'};
  const deepseek=draftFor('deepseek',saved);
  assert.equal(deepseek.apiKey,'');assert.equal(deepseek.baseURL,'https://api.deepseek.com');
  assert.equal(draftFor('mimo',saved).apiKey,'PRIVATE_TEST_KEY');
  assert.equal(draftFor('mimo',saved).model,'my-model');
  for (const id of ['claude','codebuddy','codex-private']) {
    const c=connectionFor(draftFor(id,saved));assert.equal(c.apiKey,'');assert.equal(c.baseURL,'');
  }
});

test('custom addresses and custom models survive without misleading source labels', () => {
  const saved={provider:'api-compatible',model:'custom/model',baseURL:'https://gateway.example/v1',apiKey:'TEST_KEY'};
  assert.equal(providerFor(saved),'api-compatible');
  assert.deepEqual(connectionFor(draftFor('api-compatible',saved)),saved);
  assert.equal(sourceLabel(saved),'自定义 API');
  assert.equal(providerFor({...saved,baseURL:'https://api.deepseek.com.evil.test'}),'api-compatible');
  assert.equal(providerFor({...saved,baseURL:'https://user:pass@api.deepseek.com'}),'api-compatible');
});

test('incomplete or unsafe API fields cannot start a paid probe', () => {
  const d={...draftFor('qwen'),apiKey:'TEST_KEY'};
  assert.match(draftError(d),/WorkspaceId/);
  for(const baseURL of ['https:example.com','https:/example.com','http://host/v1','https://user:pass@host/v1','https://host/v1?key=x','https://host/v1#x','https://host:444/v1']) {
    assert.ok(draftError({...d,baseURL}));
  }
  assert.equal(draftError({...d,baseURL:'https://mine.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'}),'');
  assert.ok(draftError({...draftFor('deepseek'),apiKey:''}));
});
