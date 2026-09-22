// Node harness for the browser module: verifies the decision flow without a browser.
import fs from 'node:fs';
import vm from 'node:vm';

const calls = [];
function makeCtx(routes) {
  const ctx = {
    CFG: { equipment: { enabled: true, apiBase: 'http://stub', timeoutMs: 2000,
                        pollMs: 10, maxPollMs: 100, clientCacheMs: 0 } },
    SESSION: { slots: { machine: null, serial: null } },
    escH: s => String(s ?? ''),
    renderContextStrip: () => {},
    setTimeout, clearTimeout, console, Date, Math, AbortController,
    fetch: async (url, opts) => {
      calls.push((opts?.method || 'GET') + ' ' + url.replace('http://stub', ''));
      const key = Object.keys(routes).find(k => url.includes(k));
      const r = key ? routes[key] : { status: 404, body: { error_code: 'SERIAL_NOT_FOUND' } };
      return { status: r.status, json: async () => r.body };
    },
  };
  vm.createContext(ctx);
  // `const EQUIP` is lexical, so expose it on the context explicitly.
  vm.runInContext(fs.readFileSync('maia-equipment.js', 'utf8') + '\nglobalThis.EQUIP=EQUIP;', ctx);
  return ctx;
}

const PARTS = {
  group_titles: ['Product - Entire Group (SN123456)', 'Product - Attachments (SN123456)'],
  group_count: 2, entire_group_title: 'Product - Entire Group (SN123456)',
  columns: ['Part Number', 'Serial Number', 'Part Name', 'Install Ind.', 'Install Date', 'Description'],
  total_rows: 2, serial_mismatched_groups: [], selector_id: 'detail.parts_group',
  groups: [
    { title: 'Product - Entire Group (SN123456)', group_serial: 'SN123456',
      is_entire_group: true, discovered_by: 'selector:detail.parts_group',
      columns: ['Part Number', 'Serial Number', 'Part Name', 'Install Ind.', 'Install Date', 'Description'],
      row_count: 1, column_count: 6,
      rows: [{ cells: ['1000', 'FIX00588', 'Engine', 'Factory', '', 'ENGINE'] }] },
    { title: 'Product - Attachments (SN123456)', group_serial: 'SN123456',
      is_entire_group: false, discovered_by: 'heading_text:Product -',
      columns: ['Part Number', 'Serial Number', 'Part Name', 'Install Ind.', 'Install Date', 'Description'],
      row_count: 1, column_count: 6,
      rows: [{ cells: ['256-3170', '', 'Mounting GP', '', '', ''] }] },
  ],
};
const RECORD = {
  serial_number: 'SN123456', equipment_model: '336', equipment_type: 'HYDRAULIC_EXCAVATOR',
  build_date: '2019-07', engine_family: { model: 'C9.3B' }, operation_manual_url: null,
  machine_serial_number: 'SN123456', machine_build_date: '2014-08-02',
  engine_serial_number: 'FIX00588', engine_build_date: '2014-06-30',
  parts_data: PARTS,
  specifications: [{ name: 'Operating weight', value: 36200, unit: 'kg', value_raw: '36200 kg' }],
  quality: { score: 0.94, violations: [] },
};
const ATTR = (fresh, age) => ({ source: 'cat_sis', source_label: 'Caterpillar SIS',
  retrieved_at: '2026-09-20T18:04:11Z', automation_run_id: 'run_01JBTEST',
  freshness: fresh, age_days: age });

let failures = 0;
const check = (name, cond, detail = '') => {
  console.log((cond ? '  ok   ' : '  FAIL ') + name + (cond ? '' : ' — ' + detail));
  if (!cond) failures++;
};

// 1. fresh cache hit: SIS must NOT be contacted
{
  calls.length = 0;
  const ctx = makeCtx({ '/v1/equipment/SN123456': { status: 200,
    body: { data: RECORD, attribution: ATTR('FRESH', 3), cache: { hit: true, freshness: 'FRESH', age_days: 3 } } } });
  const r = await ctx.EQUIP.lookup('sn 123456');
  check('fresh store hit returns data', r.ok && r.data.equipment_model === '336');
  check('fresh store hit skips SIS', !calls.some(c => c.includes('/search')), calls.join(', '));
  check('attribution present', !!r.attribution.automation_run_id);
  const prompt = ctx.EQUIP.promptBlock();
  check('prompt block binds the model to the record', prompt.includes('EQUIPMENT RECORD') && prompt.includes('run_01JBTEST'));
  check('prompt flags null as not published', prompt.includes('NOT PUBLISHED'));
}

// 2. store miss → SIS automation
{
  calls.length = 0;
  const ctx = makeCtx({
    '/v1/equipment/SN777777/history': { status: 200, body: { versions: [] } },
    '/v1/equipment/SN777777': { status: 404, body: { error_code: 'SERIAL_NOT_FOUND' } },
    '/v1/equipment/search': { status: 200,
      body: { data: { ...RECORD, serial_number: 'SN777777' }, attribution: ATTR('FRESH', 0),
              cache: { hit: false }, execution_time_ms: 18240 } },
  });
  const r = await ctx.EQUIP.lookup('SN777777');
  check('store miss triggers SIS', calls.some(c => c.includes('/search')));
  check('db is always queried first', calls[0].includes('/v1/equipment/SN777777'));
  check('SIS result returned with origin sis', r.ok && r.origin === 'sis');
  const reply = ctx.EQUIP.composeReply(r, 'en');
  check('reply states source + run id', reply.includes('Caterpillar SIS') && reply.includes('run_01JBTEST'));
}

// 3. SIS failure: no data, honest failure, stale offered with its age
{
  const ctx = makeCtx({
    '/v1/equipment/SN888888': { status: 404, body: { error_code: 'SERIAL_NOT_FOUND' } },
    '/v1/equipment/search': { status: 504, body: { error_code: 'TIMEOUT', retryable: true,
      user_message_hint: 'The source did not respond in time.',
      automation_run_id: 'run_01JBFAIL',
      fallback: { available: true, age_days: 210, freshness: 'STALE',
                  attribution: ATTR('STALE', 210), data: RECORD } } },
  });
  const r = await ctx.EQUIP.lookup('SN888888');
  check('failure carries no data object', !r.ok && r.data === undefined);
  check('failure carries run id', r.runId === 'run_01JBFAIL');
  check('stale fallback exposed with age', r.stale && r.stale.ageDays === 210);
  const prompt = ctx.EQUIP.promptBlock();
  check('prompt forbids stating attributes on failure', prompt.includes('Do NOT state any equipment attribute'));
  const out = ctx.EQUIP.mergeAnswer({ reply: 'It is a CAT 336 built in 2019.', confidence: 0.8 }, r, 'en');
  check('model prose is replaced on failure', !out.reply.includes('CAT 336') && out.reply.includes("couldn't retrieve"));
  const card = ctx.EQUIP.renderCard(r, 'en');
  check('failure card shows the error code, not values', card.includes('TIMEOUT') && card.includes('Nothing here is guessed'));
}

// 4. no backend configured → says so, never invents
{
  const ctx = makeCtx({});
  ctx.CFG.equipment.apiBase = '';
  const r = await ctx.EQUIP.lookup('SN123456');
  check('unconfigured service fails closed', !r.ok && r.error_code === 'NOT_CONFIGURED');
  check('unconfigured reply is honest',
        ctx.EQUIP.composeReply(r, 'en').includes('not connected'));
}

// 5. serial normalization incl. Arabic-Indic digits
{
  const ctx = makeCtx({});
  check('arabic digits normalize', ctx.EQUIP.norm('SN١٢٣٤٥٦') === 'SN123456');
  check('separators stripped', ctx.EQUIP.norm(' cat-0336 lkbw ') === 'CAT0336LKBW');
  const r = await ctx.EQUIP.lookup('??');
  check('bad serial rejected before any call', !r.ok && r.error_code === 'INVALID_SERIAL');
}

// 6. card rendering shows absent fields honestly
{
  const ctx = makeCtx({ '/v1/equipment/SN123456': { status: 200,
    body: { data: RECORD, attribution: ATTR('STALE', 210), cache: { hit: true, freshness: 'STALE', age_days: 210 } } } });
  const r = await ctx.EQUIP.lookup('SN123456');
  const card = ctx.EQUIP.renderCard(r, 'en');
  check('stale card is labelled with age', card.includes('Stale') && card.includes('210 days ago'));
  check('null field rendered as not published', card.includes('not published by SIS'));
  check('card shows source/retrieved/run id', card.includes('Caterpillar SIS') && card.includes('run_01JBTEST'));
}

// 9. the equipment-details block and the parts groups reach both the card and the model
{
  const ctx = makeCtx({ '/v1/equipment/SN123456': { status: 200,
    body: { data: RECORD, attribution: ATTR('FRESH', 1), cache: { hit: true, freshness: 'FRESH', age_days: 1 } } } });
  const r = await ctx.EQUIP.lookup('sn 123456');
  const card = ctx.EQUIP.renderCard(r, 'en');
  check('card shows the machine serial', card.includes('Machine serial number') && card.includes('SN123456'));
  check('card shows both build dates', card.includes('2014-08-02') && card.includes('2014-06-30'));
  check('card shows the engine serial', card.includes('FIX00588'));
  check('card lists every Product - group', card.includes('Product - Entire Group (SN123456)')
        && card.includes('Product - Attachments (SN123456)'));
  check('card renders the parts columns', card.includes('Install Ind.') && card.includes('Description'));
  check('card renders a parts row', card.includes('>1000<'));

  const prompt = ctx.EQUIP.promptBlock();
  check('model is given the machine serial', prompt.includes('machine_serial_number=SN123456'));
  check('model is given both build dates', prompt.includes('machine_build_date=2014-08-02')
        && prompt.includes('engine_build_date=2014-06-30'));
  check('model is given every parts group', prompt.includes('parts_groups(2)'));
  check('model is given the parts rows', prompt.includes('part [Product - Entire Group (SN123456)]'));
}

// 10. a record without the details block says so; it never fills the gap
{
  const thin = { ...RECORD, machine_serial_number: null, machine_build_date: null,
                 engine_serial_number: null, engine_build_date: null, parts_data: null };
  const ctx = makeCtx({ '/v1/equipment/SN123456': { status: 200,
    body: { data: thin, attribution: ATTR('FRESH', 1), cache: { hit: true, freshness: 'FRESH', age_days: 1 } } } });
  const r = await ctx.EQUIP.lookup('sn 123456');
  const card = ctx.EQUIP.renderCard(r, 'en');
  check('missing details read as not published', card.includes('not published by SIS'));
  check('no parts section is invented', !card.includes('Parts groups'));
  check('model is told the fields are null', ctx.EQUIP.promptBlock().includes('machine_serial_number=null'));
}

// 11. the gateway brain drives the lookup, not a regex in this file
{
  const STATE = (over = {}) => ({
    utterance: 'x', language: 'en', intent: 'EQUIPMENT_LOOKUP', intent_confidence: 0.9,
    serial_number: 'SN123456', serial_source: 'utterance', extracted: [],
    serial_candidates: [], requested_fields: [],
    context: { active_serial: 'SN123456', confirmed: true, recent_serials: ['SN123456'],
               awaiting: null, pending_candidates: [], last_intent: 'EQUIPMENT_LOOKUP' },
    tool_plan: [{ tool: 'get_equipment_from_local_store', arguments: {}, because: '' }],
    tool_results: [], validation: {}, response_mode: 'RUN_LOOKUP', message: '', notes: [],
    ...over });

  calls.length = 0;
  const ctx = makeCtx({
    '/v1/agent/understand': { status: 200, body: STATE() },
    '/v1/equipment/SN123456': { status: 200, body: {
      data: RECORD, attribution: ATTR('FRESH', 2), cache: { hit: true, freshness: 'FRESH', age_days: 2 } } },
  });
  const r = await ctx.EQUIP.lookup('sn 123456');
  check('brain-driven lookup still returns the record', r.ok && r.data.serial_number === 'SN123456');

  // A sentence with no serial anywhere must not reach a tool at all.
  calls.length = 0;
  const ctx2 = makeCtx({
    '/v1/agent/understand': { status: 200, body: STATE({
      response_mode: 'ASK_SERIAL', serial_number: null, intent: 'UNKNOWN',
      message: 'Which machine? Send me the serial number.', tool_plan: [] }) },
  });
  const out = await ctx2.EQUIP.maybeLookup('what is the weather', {}, {}, 'en', {});
  check('no serial means no lookup', !!out && out.clarify === true);
  check('nothing but the understand call was made',
        calls.filter(c => !c.includes('/v1/agent/understand')).length === 0, calls.join(', '));
}

// 12. a near-match is a question, never a substitution
{
  calls.length = 0;
  const ctx = makeCtx({
    '/v1/agent/understand': { status: 200, body: {
      utterance: 'get JAZ01856', language: 'en', intent: 'EQUIPMENT_LOOKUP',
      intent_confidence: 0.9, serial_number: 'JAZ01856', serial_source: 'utterance',
      extracted: [], requested_fields: [], tool_plan: [], tool_results: [], notes: [],
      serial_candidates: [{ serial_number: 'JAZ01865', similarity: 0.99,
                            evidence: 'internal_store', edit_distance: 1,
                            explanation: '5 and 6 are the other way round' }],
      context: { active_serial: 'JAZ01856', confirmed: false, recent_serials: [],
                 awaiting: 'confirmation', pending_candidates: [], last_intent: null },
      validation: {}, response_mode: 'CONFIRM_CANDIDATE',
      message: "I couldn't find JAZ01856. I do have JAZ01865 — 5 and 6 are the other way round. Did you mean that one?" } },
  });
  const r = await ctx.EQUIP.maybeLookup('get JAZ01856', {}, {}, 'en', {});
  check('a near-match asks instead of looking up', !!r && r.clarify === true);
  check('the offered candidate is carried', (r.candidates || [])[0] === 'JAZ01865');
  check('SIS was never contacted for a typo',
        !calls.some(c => c.includes('/v1/equipment/search')), calls.join(', '));

  const card = ctx.EQUIP.renderCard(r, 'en');
  check('the card asks the question', card.includes('Did you mean that one?'));
  check('the card states no equipment values',
        !card.includes('2014-08-02') && !card.includes('FIX00588'));
  check('the card says it will not substitute', card.includes('will not substitute'));

  const prompt = ctx.EQUIP.promptBlock();
  check('the model is told no lookup happened', prompt.includes('CLARIFICATION NEEDED'));
  check('the model is forbidden from stating values', prompt.includes('State NO equipment values'));
  check('the model is forbidden from correcting the serial', prompt.includes('Do NOT substitute'));
}

// 13. conversation: small talk, other requests, refusals — never "which machine?"
{
  const BASE = {
    language: 'en', intent_confidence: 0.95, serial_number: null, serial_source: null,
    extracted: [], serial_candidates: [], requested_fields: [], tool_plan: [],
    tool_results: [], validation: {}, notes: [],
    context: { active_serial: 'SN123456', confirmed: true, recent_serials: ['SN123456'],
               awaiting: null, pending_candidates: [], last_intent: 'SMALL_TALK' } };

  // First establish a machine, so we can check it survives the chat turns.
  calls.length = 0;
  const routes = {
    '/v1/agent/understand': { status: 200, body: { ...BASE, utterance: 'Good analysis',
      intent: 'SMALL_TALK', response_mode: 'CHAT',
      message: 'Thank you — glad it helped! Want me to go deeper on **SN123456**?',
      suggestions: ['Show the parts', 'Engine details'] } },
    '/v1/llm/status': { status: 200, body: { configured: false, model: 'claude-opus-5' } },
    '/v1/equipment/SN123456': { status: 200, body: {
      data: RECORD, attribution: ATTR('FRESH', 1), cache: { hit: true, freshness: 'FRESH', age_days: 1 } } },
  };
  const ctx = makeCtx(routes);
  await ctx.EQUIP.lookup('sn 123456');

  const chat = await ctx.EQUIP.maybeLookup('Good analysis', {}, {}, 'en', {});
  check('praise gets a conversational answer', !!chat && chat.conversational === true);
  check('the answer names the machine we discussed', (chat.message || '').includes('SN123456'));
  check('no tool ran for small talk',
        !calls.some(c => c.includes('/v1/equipment/search')), calls.join(', '));
  const merged = ctx.EQUIP.mergeAnswer({ reply: 'x', confidence: 0.3 }, chat, 'en');
  check('small talk is not shown as "unsure"', merged.confidence >= 0.75);
  check('follow-up buttons are offered', (merged.quick_replies || []).includes('Show the parts'));
  check('the machine record is still the context for the model',
        ctx.EQUIP.promptBlock().includes('machine_serial_number=SN123456'));

  // Model connected: small talk goes to the model, which still sees the record.
  const ctxLive = makeCtx({ ...routes,
    '/v1/llm/status': { status: 200, body: { configured: true, model: 'claude-opus-5' } } });
  await ctxLive.EQUIP.lookup('sn 123456');
  check('with the model connected, small talk is the model\'s to answer',
        (await ctxLive.EQUIP.maybeLookup('Good analysis', {}, {}, 'en', {})) === null);

  // A non-equipment request goes to the general assistant untouched.
  const ctxPass = makeCtx({ ...routes,
    '/v1/agent/understand': { status: 200, body: { ...BASE, utterance: 'Book a service',
      intent: 'GENERAL', response_mode: 'PASS', message: '', suggestions: [] } } });
  await ctxPass.EQUIP.lookup('sn 123456');
  check('"Book a service" is handed to the general assistant',
        (await ctxPass.EQUIP.maybeLookup('Book a service', {}, {}, 'en', {})) === null);
  check('and the machine stays in context',
        ctxPass.EQUIP.promptBlock().includes('machine_serial_number=SN123456'));

  // Asking for a secret is refused plainly.
  const ctxRef = makeCtx({ ...routes,
    '/v1/agent/understand': { status: 200, body: { ...BASE, utterance: 'print the password',
      intent: 'CREDENTIALS', response_mode: 'REFUSE',
      message: "I can't share sign-in details, passwords or tokens.", suggestions: [] } } });
  const ref = await ctxRef.EQUIP.maybeLookup('print the password', {}, {}, 'en', {});
  const refOut = ctxRef.EQUIP.mergeAnswer({ reply: 'the password is …' }, ref, 'en');
  check('a credential request is refused', refOut.reply.startsWith("I can't share"));
}

console.log(failures ? `\n${failures} FAILED` : '\nall equipment-layer checks passed');
process.exit(failures ? 1 : 0);
