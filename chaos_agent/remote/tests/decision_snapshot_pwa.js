"";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {browser, response} = require('./test_pwa.js');
const data = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
(async () => {
  const b = browser();
  await b.evaluate('sync()');
  b.fixture.projects = data.projects.projects;
  b.fixture.sessionRows = [data.before.session];
  b.fixture.status = data.beforeStatus;
  b.fixture.history.set(data.sessionId, data.before);
  b.fixture.requests.set(data.sessionId, data.pending);
  await b.evaluate('sync()');
  await b.evaluate('openSession(' + JSON.stringify(data.sessionId) + ')');
  const route = b.route;
  b.route = async (url, options) => {
    if (url.endsWith('/respond')) {
      b.fixture.status = data.afterStatus;
      b.fixture.history.set(data.sessionId, data.after);
      b.fixture.requests.set(data.sessionId, data.afterRequests);
      return response(data.response);
    }
    return route(url, options);
  };
  await b.evaluate("respondCard(requestCards.find(c=>c.preview.operation==='accept_partial'),true)");
  assert.equal(b.node('chatState').textContent, '已接受部分 · 未验证');
  await b.evaluate('sync()');
  assert.equal(b.node('chatState').textContent, '已接受部分 · 未验证');
  const fresh = browser();
  await fresh.evaluate('sync()');
  fresh.fixture.projects = data.projects.projects;
  fresh.fixture.sessionRows = [data.after.session];
  fresh.fixture.status = data.afterStatus;
  fresh.fixture.history.set(data.sessionId, data.after);
  fresh.fixture.requests.set(data.sessionId, data.afterRequests);
  await fresh.evaluate('sync()');
  await fresh.evaluate('openSession(' + JSON.stringify(data.sessionId) + ')');
  assert.equal(fresh.node('chatState').textContent, '已接受部分 · 未验证');
  const lost = browser();
  await lost.evaluate('sync()');
  lost.fixture.projects = data.projects.projects;
  lost.fixture.sessionRows = [data.before.session];
  lost.fixture.status = data.beforeStatus;
  lost.fixture.history.set(data.sessionId, data.before);
  lost.fixture.requests.set(data.sessionId, data.pending);
  await lost.evaluate('sync()');
  await lost.evaluate('openSession(' + JSON.stringify(data.sessionId) + ')');
  const lostRoute = lost.route;
  lost.route = async (url, options) => {
    if (url.endsWith('/respond')) {
      lost.fixture.status = data.afterStatus;
      lost.fixture.history.set(data.sessionId, data.after);
      lost.fixture.requests.set(data.sessionId, data.afterRequests);
      throw Error('response lost after durable consumption');
    }
    return lostRoute(url, options);
  };
  await lost.evaluate("respondCard(requestCards.find(c=>c.preview.operation==='accept_partial'),true)");
  await lost.evaluate('sync()');
  assert.equal(lost.node('chatState').textContent, '已接受部分 · 未验证');
  const other = browser();
  await other.evaluate('sync()');
  other.fixture.projects = data.projects.projects;
  other.fixture.sessionRows = [data.after.session];
  other.fixture.status = {status:'running',task_id:'unrelated-run',session_id:'other-session',sequence:1};
  other.fixture.history.set(data.sessionId, data.after);
  other.fixture.requests.set(data.sessionId, data.afterRequests);
  await other.evaluate('sync()');
  await other.evaluate('openSession(' + JSON.stringify(data.sessionId) + ')');
  assert.equal(other.evaluate('globalRun.task_id'), 'unrelated-run');
  assert.equal(other.node('activeBanner').hidden, false);
  assert.equal(other.node('chatState').textContent, '已接受部分 · 未验证');
  console.log('Real HTTP snapshots -> PWA response/sync/fresh navigation: accepted_partial label confirmed; HTTP result remains unverified.');
})().catch(error => {console.error(error); process.exitCode = 1;});
