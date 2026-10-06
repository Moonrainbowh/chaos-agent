const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
const response = (data, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => data});
const plain = value => JSON.parse(JSON.stringify(value));
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
const textOf = node => node.children?.length ? node.children.map(textOf).join('') : node.textContent;
const summary = (id, projectId = 'p1', status = 'completed') => ({id, title: '会话 ' + id, preview: '已有内容 ' + id, project_id: projectId, project_name: projectId === 'p1' ? '项目一' : '项目二', updated_at: 1791000000, status, message_count: 2});

function browser({protocol = 'https:', host = 'chaos.example.com', saved = 'saved-device', selection = null, drafts = null} = {}) {
  const elements = new Map(), storage = new Map(saved ? [['chaos-device', saved]] : []), sockets = [], calls = [], timers = new Map();
  if (selection) storage.set('chaos-selection', JSON.stringify(selection));
  if (drafts) storage.set('chaos-drafts-v1', JSON.stringify(drafts));
  let timerId = 0;
  const element = (tagName = 'div') => ({
    tagName, textContent: '', value: '', hidden: false, disabled: false, children: [], dataset: {}, style: {}, attributes: {},
    append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children = children; },
    setAttribute(key, value) { this.attributes[key] = value; }, scrollTop: 0, scrollHeight: 500, clientHeight: 500
  });
  const fixture = {
    status: {status: 'idle', task_id: null, session_id: null, sequence: 0},
    projects: [{id: 'p1', name: '项目一', path: 'F:/one', session_count: 3, updated_at: 1791000000, available: true}, {id: 'p2', name: '项目二', path: 'F:/two', session_count: 1, updated_at: 1791000001, available: true}],
    sessionRows: [summary('s1'), summary('s2', 'p2')], nextOffset: null, history: new Map(), newId: 'new-session', continueId: null, requests: new Map()
  };
  fixture.history.set('s1', {messages: [{sequence: 3, role: 'user', content: '已有问题'}, {sequence: 4, role: 'assistant', content: '已有回答'}], next_before: 3, session: summary('s1'), task: null, active_task: null, event_sequence: 0, assistant_open: false});
  fixture.history.set('s2', {messages: [{sequence: 1, role: 'user', content: '项目二问题'}, {sequence: 2, role: 'assistant', content: '项目二回答'}], next_before: null, session: summary('s2', 'p2'), task: null, active_task: null, event_sequence: 0, assistant_open: false});
  const route = async (url, options = {}) => {
    const parsed = new URL(url, 'https://host.test'), method = options.method || 'GET';
    if (parsed.pathname === '/status') return response({...fixture.status});
    const pending = parsed.pathname.match(/^\/sessions\/([^/]+)\/requests$/);
    if (pending) return response(fixture.requests.get(pending[1]) || {task_id:null,requests:[]});
    if (/^\/tasks\/[^/]+\/requests\/[^/]+\/respond$/.test(parsed.pathname)) {
      const body = JSON.parse(options.body), id = parsed.pathname.split('/')[4];
      for (const snapshot of fixture.requests.values()) for (const card of snapshot.requests) if (card.request_id===id) card.status=body.approved?'approved':'denied';
      return response({status:'recorded'});
    }
    if (parsed.pathname === '/projects' && method === 'GET') return response({projects: fixture.projects, current_project_id: 'p1'});
    if (parsed.pathname === '/projects' && method === 'POST') {
      const root = JSON.parse(options.body).path;
      const project = {id:'p3',name:'新增项目',path:root,available:true,registered:true,session_count:0};
      fixture.projects.push(project);return response({project});
    }
    if (/^\/projects\/[^/]+\/select$/.test(parsed.pathname)) return response({project:fixture.projects.find(p=>p.id===parsed.pathname.split('/')[2]),recent:true});
    if (/^\/projects\/[^/]+$/.test(parsed.pathname) && method === 'DELETE') {
      const id=parsed.pathname.split('/')[2];fixture.projects=fixture.projects.filter(p=>p.id!==id);return response({removed_project_id:id});
    }
    if (parsed.pathname === '/project-directories') {
      const root=parsed.searchParams.get('path');return response({path:root,parent:root?'F:/':null,directories:[{name:'目录一',path:'F:/one'}]});
    }
    if (parsed.pathname === '/pair') return response({device_credential: 'new-device'});
    if (parsed.pathname === '/sessions' && method === 'GET') {
      let rows = fixture.sessionRows;
      const projectId = parsed.searchParams.get('project_id'), query = parsed.searchParams.get('q');
      if (projectId) rows = rows.filter(item => item.project_id === projectId);
      if (query) rows = rows.filter(item => (item.title + item.preview).includes(query));
      return response({sessions: rows, next_offset: fixture.nextOffset});
    }
    if (parsed.pathname === '/sessions' && method === 'POST') {
      const projectId = JSON.parse(options.body).project_id, id = fixture.newId;
      fixture.history.set(id, {messages: [], next_before: null, session: {...summary(id, projectId, 'idle'), title: '新会话'}, task: null, active_task: null, event_sequence: 0});
      return response({session_id: id, project_id: projectId});
    }
    const match = parsed.pathname.match(/^\/sessions\/([^/]+)\/messages$/);
    if (match && method === 'GET') return response(fixture.history.get(decodeURIComponent(match[1])) || {messages: [], session: summary(decodeURIComponent(match[1])), next_before: null, task: null, active_task: null, event_sequence: 0});
    if (match && method === 'POST') {
      const oldId = decodeURIComponent(match[1]), id = fixture.continueId || oldId, old = fixture.history.get(oldId), prompt = JSON.parse(options.body).prompt;
      fixture.status = {status: 'running', task_id: 'new-task', session_id: id, sequence: 1};
      fixture.history.set(id, {...old, session: {...old.session, id, status: 'running'}, messages: [...old.messages, {sequence: null, role: 'user', content: prompt}], active_task: {id: 'new-task', session_id: id, status: 'running'}, task: {id: 'new-task', status: 'running'}, event_sequence: 1, assistant_open: false});
      return response({task_id: 'new-task', session_id: id, ...(id !== oldId ? {continued_from: oldId} : {})});
    }
    if (/^\/tasks\/[^/]+\/stop$/.test(parsed.pathname)) { fixture.status.status = 'stopped'; return response({status: 'stopped'}); }
    throw Error('Unexpected request ' + method + ' ' + url);
  };
  const context = vm.createContext({
    location: {protocol, host}, URLSearchParams, Date,
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    document: {querySelector: key => {if (!elements.has(key)) elements.set(key, element()); return elements.get(key);}, createElement: element, createTextNode: text => ({textContent: text}), addEventListener() {}},
    window: {addEventListener() {}},
    setTimeout: callback => {const id = ++timerId; timers.set(id, callback); return id;}, clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => { calls.push({url, options}); return await b.route(url, options); },
    WebSocket: class {constructor(url) {this.url = url; this.readyState = 0; sockets.push(this);} close() {this.readyState = 3;} send(value) {this.auth = value;}}
  });
  const b = {context, storage, elements, sockets, calls, fixture, route, timers, evaluate: code => vm.runInContext(code, context), node: id => elements.get('#' + id), count: pathname => calls.filter(call => new URL(call.url, 'https://host.test').pathname === pathname).length};
  vm.runInContext(script, context);
  return b;
}

function activate(b, sessionId = 's1', sequence = 12) {
  b.fixture.status = {status: 'running', task_id: 'task-1', session_id: sessionId, sequence};
  const history = b.fixture.history.get(sessionId);
  b.fixture.history.set(sessionId, {...history, session: {...history.session, status: 'running'}, messages: [...history.messages, {sequence: null, role: 'user', content: '当前问题'}, {sequence: null, role: 'assistant', content: '当前回复'}], active_task: {id: 'task-1', session_id: sessionId, status: 'running'}, task: {id: 'task-1', status: 'running'}, event_sequence: sequence, assistant_open: true});
}
function event(b, name, sequence, data = {}, sessionId = 's1', taskId = 'task-1', socket = b.sockets.at(-1)) {
  socket.onmessage({data: JSON.stringify({event: name, sequence, task_id: taskId, session_id: sessionId, data})});
}

async function credentialChecks() {
  for (const [protocol, host, expected] of [['https:', 'chaos.example.com', 'wss://chaos.example.com'], ['http:', '192.168.1.10:8787', 'ws://192.168.1.10:8787']]) {
    const b = browser({protocol, host}); await b.evaluate('sync()');
    assert.equal(b.node('pair').hidden, true); assert.equal(b.node('state').textContent, '● 在线');
    activate(b); await b.evaluate("openSession('s1')");
    assert.equal(b.sockets.at(-1).url, expected + '/sessions/s1/events?since=12');
    b.sockets.at(-1).onopen(); assert.equal(b.sockets.at(-1).auth, 'auth:saved-device');
    for (const failure of [async () => {throw Error('network');}, async () => response({error: 'offline'}, 503), async () => response({error: 'permission denied'}, 403)]) {
      b.route = failure; await b.evaluate('sync()'); assert.equal(b.storage.get('chaos-device'), 'saved-device');
    }
    b.route = async () => response({error: 'unauthorized'}, 401); await b.evaluate('sync()');
    assert.equal(b.storage.has('chaos-device'), false); assert.equal(b.node('pair').hidden, false); assert.equal(b.node('chatScreen').hidden, true);
  }
  const ws = browser(); await ws.evaluate('sync()'); activate(ws); await ws.evaluate("openSession('s1')"); ws.sockets.at(-1).onclose({code: 4401});
  assert.equal(ws.storage.has('chaos-device'), false);
  const paired = browser({saved: null}); paired.node('token').value = 'example-one-use'; await paired.evaluate('pair()');
  assert.equal(paired.storage.get('chaos-device'), 'new-device'); assert.equal(paired.node('list').children.length, 2);
  const partial = browser(); await partial.evaluate('sync()');
  const history = partial.fixture.history.get('s1');
  partial.fixture.history.set('s1', {...history, session: summary('s1', 'p1', 'accepted_partial'),
    task: {id:'partial-task',status:'accepted_partial',result:{execution_status:'accepted_partial',verification_status:'unverified'}}});
  await partial.evaluate("openSession('s1')");
  assert.equal(partial.node('chatState').textContent, '已接受部分');
  partial.evaluate("globalRun.session_id='s1'; showEvent({event:'task_status',data:{status:'accepted_partial',result:{execution_status:'accepted_partial',verification_status:'unverified'}}})");
  assert.equal(partial.node('chatState').textContent, '已接受部分 · 未验证');
  assert.equal(partial.evaluate("statusText('waiting_decision')"), '待决策');
  console.log('Credentials and origins: HTTP/WS, HTTPS/WSS, saved auth, offline/503, HTTP 401 and WS 4401.');
}

async function navigationChecks() {
  const b = browser(); await b.evaluate('sync()');
  assert.equal(b.node('list').children.length, 2); assert.match(textOf(b.node('list').children[1]), /项目二/);
  b.fixture.nextOffset = 40; await b.evaluate('loadSessions()'); assert.equal(b.node('loadMore').hidden, false);
  b.fixture.sessionRows = [summary('s2', 'p2'), summary('s3')]; b.fixture.nextOffset = null; await b.evaluate('loadSessions(true)');
  assert.equal(b.node('list').children.length, 3); assert.match(b.calls.at(-1).url, /offset=40/); assert.equal(b.node('loadMore').hidden, true);
  b.node('search').value = '已有内容 s2'; await b.evaluate('loadSessions()');
  assert.equal(b.node('list').children.length, 1); assert.equal(new URL(b.calls.at(-1).url, 'https://h').searchParams.get('q'), '已有内容 s2');
  await b.evaluate("showHome('projects')"); assert.equal(b.node('list').children.length, 2);
  b.node('search').value = '项目二'; b.evaluate('renderList()'); assert.equal(b.node('list').children.length, 1);
  await b.node('list').children[0].onclick(); assert.equal(b.evaluate('screen'), 'project'); assert.equal(b.node('projectTitle').textContent, '项目二');
  assert.equal(new URL(b.calls.findLast(call=>call.url.startsWith('/sessions?')).url, 'https://h').searchParams.get('project_id'), 'p2');
  await b.evaluate('newSession()'); const create = b.calls.find(call => call.url === '/sessions' && call.options?.method === 'POST');
  assert.deepEqual(JSON.parse(create.options.body), {project_id: 'p2'}); assert.equal(b.evaluate('selectedSessionId'), 'new-session'); assert.equal(b.node('chatProject').textContent, '项目二');
  await b.evaluate('backFromChat()'); assert.equal(b.evaluate('selectedProjectId'), 'p2');
  console.log('Navigation: recent projects and sessions, content search, pagination, project-scoped creation and back navigation.');
}

async function historyChecks() {
  const b = browser({selection: {screen: 'chat', project_id: 'p1', session_id: 's1'}}); await b.evaluate('sync()');
  assert.equal(b.evaluate('selectedSessionId'), 's1'); assert.equal(b.node('messages').children.length, 2);
  const count = b.count('/sessions/s1/messages'); await b.evaluate('sync()'); await b.evaluate('sync()');
  assert.equal(b.count('/sessions/s1/messages'), count, 'polling must not reload history'); assert.equal(b.count('/projects'), 1, 'polling must not reload project catalogue');
  const original = b.route; b.route = async (url, options) => {
    if (url.includes('/sessions/s1/messages?') && url.includes('before=3')) return response({messages: [{sequence: 1, role: 'user', content: '更早问题'}, {sequence: 2, role: 'assistant', content: '更早回答'}, {sequence: 3, role: 'user', content: '已有问题'}, {sequence: 99, role: 'system', content: '隐藏系统'}, {sequence: 100, role: 'tool', content: '隐藏工具'}, {sequence: 101, role: 'reasoning', content: '隐藏推理'}], next_before: null});
    return original(url, options);
  };
  await b.evaluate('loadHistory(nextBefore)'); assert.equal(b.node('messages').children.length, 4); assert.equal(b.node('earlier').hidden, true);
  assert.deepEqual(plain(b.evaluate('records.map(row=>row.sequence)')), [1, 2, 3, 4]);
  const saved = JSON.parse(b.storage.get('chaos-selection')); assert.equal(saved.session_id, 's1');
  const restored = browser({selection: saved}); await restored.evaluate('sync()'); assert.equal(restored.node('chatScreen').hidden, false); assert.equal(restored.node('messages').children.length, 2);
  const restoredRoute=restored.route;
  restored.route=async(url,options)=>url.startsWith('/sessions/s1/messages?')?response({error:'session changed during history snapshot; retry'},409):restoredRoute(url,options);
  await restored.evaluate('loadHistory()');
  assert.equal(restored.storage.get('chaos-device'),'saved-device');assert.equal(restored.evaluate('historyPending'),true);
  restored.route=restoredRoute;await restored.evaluate('sync()');
  assert.equal(restored.evaluate('historyPending'),false);assert.equal(restored.node('messages').children.length,2);
  console.log('History: refresh recovery, older-message paging, durable sequence deduplication, public roles only, no polling reload.');
}

async function streamChecks() {
  const b = browser(); await b.evaluate('sync()'); activate(b); await b.evaluate("openSession('s1')");
  const firstSocket = b.sockets.at(-1), bubbles = b.node('messages').children;
  assert.equal(bubbles.length, 4); assert.equal(b.evaluate('assistantText'), '当前回复');
  event(b, 'assistant_delta', 13, {text: '继续'}); assert.equal(bubbles.length, 4); assert.equal(b.evaluate('assistantText'), '当前回复继续');
  event(b, 'assistant_delta', 13, {text: '重复'}); event(b, 'assistant_delta', 100, {text: '错误会话'}, 's2'); event(b, 'assistant_delta', 101, {text: '错误任务'}, 's1', 'other-task');
  assert.equal(b.evaluate('lastSequence'), 13); assert.equal(b.evaluate('assistantText'), '当前回复继续');
  event(b, 'tool_started', 14, {name: 'read_file'}); event(b, 'assistant_delta', 15, {text: '# 标题\n\n1. **步骤**\n2. `命令`\n\n```html\n\n<script>alert(1)</script>\n```'});
  assert.equal(bubbles.length, 5, 'tool boundaries must split assistant segments without showing private tool output');
  const formatted = bubbles.at(-1); assert.deepEqual(formatted.children.map(node => node.tagName), ['h2', 'ol', 'pre']);
  assert.equal(formatted.children[2].children[0].textContent, '\n<script>alert(1)</script>'); assert.equal(formatted.children[2].children[0].children.length, 0);
  firstSocket.onclose({code: 1006}); await b.evaluate('sync()'); assert.match(b.sockets.at(-1).url, /since=15$/);
  await b.evaluate("openSession('s2')"); assert.equal(b.node('activeBanner').hidden, false); assert.equal(b.node('send').disabled, true); assert.equal(b.node('stop').hidden, true);
  event(b, 'assistant_delta', 200, {text: '旧连接'}, 's1', 'task-1', firstSocket); assert.equal(b.node('messages').children.length, 2); assert.equal(b.evaluate('selectedSessionId'), 's2');
  b.node('prompt').value = '不能并发'; const requests = b.calls.length; await b.evaluate('send()'); assert.equal(b.calls.length, requests);
  await b.node('viewActive').onclick(); assert.equal(b.evaluate('selectedSessionId'), 's1'); assert.equal(b.node('stop').hidden, false);
  console.log('Streaming: atomic history cursor, snapshot append, tool boundaries, safe Markdown, replay dedupe, cross-session/task filtering, one active Host task.');
}

async function terminalAndContinuationChecks() {
  const b = browser(); await b.evaluate('sync()'); activate(b); await b.evaluate("openSession('s1')");
  const before = b.count('/sessions/s1/messages');
  b.fixture.status = {status: 'completed', task_id: 'task-1', session_id: 's1', sequence: 13};
  b.fixture.history.set('s1', {messages: [{sequence: 3, role: 'user', content: '已有问题'}, {sequence: 4, role: 'assistant', content: '已有回答'}, {sequence: 5, role: 'user', content: '当前问题'}, {sequence: 6, role: 'assistant', content: '当前回复完成'}], session: summary('s1'), task: {id: 'task-1', status: 'completed'}, active_task: null, event_sequence: 0, next_before: null});
  event(b, 'task_completed', 13); await tick(); if (b.evaluate('historyJob')) await b.evaluate('historyJob.promise'); await tick();
  assert.equal(b.count('/sessions/s1/messages'), before + 1); assert.equal(b.node('messages').children.length, 4); assert.equal(textOf(b.node('messages').children.at(-1)), '当前回复完成');
  await b.evaluate('sync()'); assert.equal(b.count('/sessions/s1/messages'), before + 1);
  b.fixture.continueId = 'continued'; b.node('prompt').value = '继续这个项目'; await b.evaluate('send()');
  assert.equal(b.evaluate('selectedSessionId'), 'continued'); assert.equal(b.node('messages').children.length, 5); assert.equal(textOf(b.node('messages').children.at(-1)), '继续这个项目');
  assert.equal(b.node('messages').children.filter(node => textOf(node) === '继续这个项目').length, 1); assert.match(b.node('notice').textContent, /延续会话/); assert.match(b.sockets.at(-1).url, /\/sessions\/continued\/events\?since=1$/);
  assert.equal(JSON.parse(b.storage.get('chaos-selection')).session_id, 'continued');
  console.log('Completion and continuation: one durable reload, no duplicated user/history, new session selected and clearly labelled.');
}

async function availabilityChecks() {
  const b = browser(); await b.evaluate('sync()');
  b.fixture.projects.push({id: 'unassigned', name: '未归类', path: '', available: false, session_count: 1});
  b.fixture.history.set('legacy', {messages: [{sequence: 1, role: 'user', content: '可搜索的旧历史'}], session: {...summary('legacy', 'unassigned'), project_name: '未归类'}, task: null, active_task: null, event_sequence: 0});
  await b.evaluate("showHome('projects')"); const legacyRow = b.node('list').children.at(-1);
  assert.match(textOf(legacyRow), /未记录项目/); assert.match(textOf(legacyRow), /只读历史/); assert.doesNotMatch(textOf(legacyRow), /目录不可用/);
  await b.evaluate("openSession('legacy')"); assert.equal(b.node('prompt').disabled, true); assert.equal(b.node('send').disabled, true); assert.equal(b.node('chatState').textContent, '只读历史');
  assert.match(b.node('notice').textContent, /未记录所属项目/); assert.equal(b.node('messages').children.length, 1);
  const count = b.calls.length; b.node('prompt').value = '不应发送'; await b.evaluate('send()'); assert.equal(b.calls.length, count);
  b.fixture.projects.find(item => item.id === 'p2').available = false; await b.evaluate('loadProjects()'); await b.evaluate("openSession('s2')");
  assert.equal(b.node('send').disabled, true); assert.match(b.node('notice').textContent, /项目目录暂不可用/);
  await b.evaluate("openProject('p2')"); assert.equal(b.node('newSession').disabled, true);
  activate(b); b.fixture.status.status = 'verifying'; const active = b.fixture.history.get('s1'); active.active_task.status = 'verifying'; active.task.status = 'verifying'; active.session.status = 'verifying';
  await b.evaluate("openSession('s1')"); assert.equal(b.node('chatState').textContent, '验证中'); assert.equal(b.node('send').hidden, true); assert.equal(b.node('stop').hidden, false); assert.equal(b.node('prompt').disabled, true);
  event(b, 'task_status', 13, {status: 'verifying'}); assert.equal(b.node('chatState').textContent, '验证中');
  console.log('Availability: unassigned and missing-project history stays readable, sends/new sessions disabled; verifying remains active.');
}

async function raceChecks() {
  const b = browser(); await b.evaluate('sync()'); const old = deferred(), original = b.route;
  b.route = async (url, options) => url.startsWith('/sessions/s1/messages?') ? old.promise : original(url, options);
  const pending = b.evaluate("openSession('s1')"); await b.evaluate("openSession('s2')"); old.resolve(response(b.fixture.history.get('s1'))); await pending;
  assert.equal(b.evaluate('selectedSessionId'), 's2'); assert.equal(b.node('chatTitle').textContent, '会话 s2'); assert.equal(textOf(b.node('messages').children[0]), '项目二问题'); assert.equal(b.evaluate('selectedProjectId'), 'p2');
  await b.evaluate("showHome('recent')"); const listOld = deferred();
  b.route = async (url, options) => new URL(url, 'https://h').searchParams.get('q') === '旧' ? listOld.promise : original(url, options);
  b.node('search').value = '旧'; const search = b.evaluate('loadSessions()'); b.node('search').value = 's2'; await b.evaluate('loadSessions()');
  listOld.resolve(response({sessions: [summary('stale')], next_offset: null})); await search; assert.deepEqual(plain(b.evaluate('sessions.map(row=>row.id)')), ['s2']);
  const authOld = deferred(); b.route = async () => authOld.promise; const auth = b.evaluate("api('/projects').catch(()=>null)"); b.evaluate("credential='replacement-device'"); authOld.resolve(response({error: 'unauthorized'}, 401)); await auth;
  assert.equal(b.evaluate('credential'), 'replacement-device', 'stale auth response must not clear a newly paired credential');
  const boot = browser(); await boot.evaluate('sync()'); boot.evaluate('initialized=false'); const normal = boot.route;
  boot.route = async (url, options) => url === '/projects' ? response({error: 'offline'}, 503) : normal(url, options); await boot.evaluate('sync()'); assert.equal(boot.evaluate('initialized'), false);
  boot.route = normal; await boot.evaluate('sync()'); assert.equal(boot.evaluate('initialized'), true); assert.equal(boot.node('list').children.length, 2);
  console.log('Request races: stale history/search/auth responses ignored, offline catalogue initialization recovers.');
}

async function migratedMobileChecks() {
  const b=browser();await b.evaluate('sync()');await b.evaluate("openSession('s1')");
  b.node('prompt').value='项目一\n尚未发送';b.node('prompt').oninput();await b.evaluate("openSession('s2')");
  assert.equal(b.node('prompt').value,'','another session must not inherit the old draft');
  b.node('prompt').value='项目二草稿';b.node('prompt').oninput();await b.evaluate("showHome('projects')");await b.evaluate("openSession('s1')");
  assert.equal(b.node('prompt').value,'项目一\n尚未发送');
  const drafts=JSON.parse(b.storage.get('chaos-drafts-v1')),selection=JSON.parse(b.storage.get('chaos-selection'));
  const restored=browser({selection,drafts});await restored.evaluate('sync()');assert.equal(restored.node('prompt').value,'项目一\n尚未发送');
  await restored.evaluate('send()');assert.equal(restored.node('prompt').value,'');assert.equal(JSON.parse(restored.storage.get('chaos-drafts-v1'))['session:s1'],undefined);
  b.fixture.projects[0].registered=true;await b.evaluate('openManager()');await b.evaluate('browseDirectories()');
  assert.equal(b.node('browseFooter').hidden,true);await b.node('directoryList').children[0].onclick();
  assert.equal(b.node('projectPath').value,'F:/one');b.node('projectPath').value='F:/new';await b.evaluate('addProjectPath()');
  assert.equal(b.evaluate('selectedProjectId'),'p3');assert.equal(b.node('projectManager').hidden,true);
  await b.evaluate('openManager(true)');const items=b.node('directoryList').children;items[0].children[1].onclick();
  assert.equal(b.node('removeConfirm').hidden,false);b.node('cancelRemove').onclick();assert.equal(b.node('removeConfirm').hidden,true);
  assert.equal(b.calls.filter(c=>c.options?.method==='DELETE').length,0,'cancel must not remove');
  items[0].children[1].onclick();await b.evaluate('removeProjectEntry()');assert.equal(b.calls.filter(c=>c.options?.method==='DELETE').length,1);
  const delayed=deferred(),normal=b.route;b.route=async(url,options)=>url.startsWith('/project-directories')?delayed.promise:normal(url,options);
  const pending=b.evaluate("browseDirectories('F:/late')");b.evaluate('closeManager()');delayed.resolve(response({path:'F:/late',parent:'F:/',directories:[]}));await pending;
  assert.equal(b.node('projectManager').hidden,true,'late browse must not reopen a closed picker');
  const race=browser();await race.evaluate('sync()');const raceNormal=race.route,projectWait=deferred();
  race.route=async(url,options)=>url.includes('/sessions?')&&url.includes('project_id=p1')?projectWait.promise:raceNormal(url,options);
  const first=race.evaluate("openProject('p1')");await race.evaluate("openProject('p2')");projectWait.resolve(response({sessions:[],next_offset:null}));await first;
  assert.equal(race.calls.filter(c=>c.url==='/projects/p1/select').length,0,'late navigation must not change persistent recent project');
  await race.evaluate("openSession('s1')");race.node('prompt').value='已发送的草稿';race.node('prompt').oninput();
  const sendWait=deferred();race.route=async(url,options)=>url==='/sessions/s1/messages'&&options?.method==='POST'?sendWait.promise:raceNormal(url,options);
  const sending=race.evaluate('send()');await race.evaluate("openSession('s2')");await race.evaluate("openSession('s1')");
  sendWait.resolve(response({task_id:'accepted',session_id:'s1'}));await sending;
  assert.equal(race.node('prompt').value,'','accepted draft must clear when navigating away and back while sending');
  const auth=browser();await auth.evaluate('sync()');auth.evaluate('openManager(true)');auth.evaluate("pendingRemove=projects[0];$('removeConfirm').hidden=false");
  const authWait=deferred(),authNormal=auth.route;auth.route=async(url,options)=>url.startsWith('/project-directories')?authWait.promise:response({error:'unauthorized'},401);
  const browsing=auth.evaluate("browseDirectories('F:/late')");try{await auth.evaluate("api('/projects')")}catch{}
  assert.equal(auth.node('projectManager').hidden,true);assert.equal(auth.node('removeConfirm').hidden,true);assert.equal(auth.evaluate('pendingRemove'),null);
  authWait.resolve(response({path:'F:/late',parent:'F:/',directories:[]}));await browsing;
  assert.equal(auth.node('projectManager').hidden,true);assert.notEqual(auth.node('projectPath').value,'F:/late');
  console.log('Migrated mobile controls: shared project add/browse/remove confirmation, stale picker response, isolated multiline drafts, reload and successful-send clearing.');
}

async function cursorResetChecks() {
  const b=browser();await b.evaluate('sync()');activate(b);b.fixture.status.host_epoch='epoch-one';b.fixture.history.get('s1').host_epoch='epoch-one';await b.evaluate("openSession('s1')");
  assert.match(b.sockets.at(-1).url,/epoch=epoch-one$/);
  const old=b.sockets.at(-1),before=b.count('/sessions/s1/messages');b.fixture.history.get('s1').event_sequence=25;
  event(b,'connection_state',25,{reset:true,snapshot_required:true},'s1','task-1',old);await tick();await tick();
  assert.equal(b.evaluate('lastSequence'),25);assert.ok(b.count('/sessions/s1/messages')>before);assert.notEqual(b.sockets.at(-1),old);
  activate(b,'s1',2);b.fixture.status.host_epoch='epoch-two';b.fixture.history.get('s1').host_epoch='epoch-two';await b.evaluate('sync()');
  assert.equal(b.evaluate('lastSequence'),2);assert.equal(b.evaluate('hostEpoch'),'epoch-two');assert.match(b.sockets.at(-1).url,/since=2&epoch=epoch-two$/);
  console.log('Event gap and Host epoch: persistent snapshot reload before low-sequence reconnect.');
}

async function approvalChecks() {
  const b=browser();await b.evaluate('sync()');
  const card={request_id:'host-uuid',task_id:'task-one',action_digest:'full-digest',state_version:'version-one',owner_instance_id:'owner-one',workspace_root:'F:/one',kind:'approval',preview:{summary:'<img src=x onerror=attack()>',notice:'有限预览'},expires_at:Date.now()/1000+300,status:'pending'};
  b.fixture.requests.set('s1',{task_id:'task-one',requests:[card]});await b.evaluate("openSession('s1')");
  assert.equal(b.node('requestsPanel').hidden,false);assert.match(textOf(b.node('requestRows')),/<img src=x onerror=attack\(\)>/);
  const normal=b.route,wait=deferred();let answers=0;
  b.route=async(url,options)=>{if(url.endsWith('/respond')){answers++;return wait.promise}return normal(url,options)};
  const first=b.evaluate('respondCard(requestCards[0],true)');await tick();await b.evaluate('respondCard(requestCards[0],true)');assert.equal(answers,1);
  const posted=JSON.parse(b.calls.find(call=>call.url.endsWith('/respond')).options.body);
  assert.deepEqual(posted,{action_digest:'full-digest',state_version:'version-one',owner_instance_id:'owner-one',approved:true});
  card.status='approved';wait.resolve(response({status:'recorded'}));await first;assert.equal(b.node('requestRows').children[0].children.some(node=>node.className==='request-controls'),false);
  card.status='pending';card.expires_at=Date.now()/1000-1;await b.evaluate('loadRequests()');assert.equal(b.node('requestRows').children[0].children.some(node=>node.className==='request-controls'),false);
  card.expires_at=Date.now()/1000+300;card.kind='decision';card.preview={operation:'reconcile',summary:'未知结果',reconciliation:{decision:'operator_not_executed'}};b.route=normal;await b.evaluate('loadRequests()');
  const before=b.count('/tasks/task-one/requests/host-uuid/respond');await b.evaluate('respondCard(requestCards[0],true)');assert.equal(b.count('/tasks/task-one/requests/host-uuid/respond'),before);
  await b.evaluate("respondCard(requestCards[0],true,'我已检查','目标文件不存在')");assert.equal(b.count('/tasks/task-one/requests/host-uuid/respond'),before+1);
  card.status='pending';const late=deferred();b.route=async(url,options)=>url==='/sessions/s1/requests'?late.promise:normal(url,options);
  const fetching=b.evaluate('loadRequests()');await b.evaluate("openSession('s2')");late.resolve(response({task_id:'task-one',requests:[card]}));await fetching;assert.equal(b.evaluate('requestCards.length'),0);
  b.route=async()=>response({error:'unauthorized'},401);await b.evaluate('loadRequests()');assert.equal(b.node('requestsPanel').hidden,true);
  console.log('Approval cards: bound response, single-flight, expiry, literal text, explicit reconciliation evidence, stale navigation and revoke.');
}

module.exports = {browser, response};
if (require.main === module) (async () => {
  await credentialChecks(); await navigationChecks(); await historyChecks(); await streamChecks(); await terminalAndContinuationChecks(); await availabilityChecks(); await raceChecks();
  await migratedMobileChecks();
  await approvalChecks();
  await cursorResetChecks();
  assert.match(html, /height:100dvh/); assert.match(html, /safe-area-inset-bottom/); assert.match(html, /visualViewport/); assert.match(html, /min-height:40px/);
  console.log('All mobile page behavior checks passed; no Provider or model calls were made.');
})().catch(error => {console.error(error); process.exitCode = 1;});
