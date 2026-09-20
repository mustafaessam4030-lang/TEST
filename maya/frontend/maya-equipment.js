/* ══════════════════════════════════════════════════════════════════════════
   MAIA — EQUIPMENT DATA LAYER (v9 addition)
   ──────────────────────────────────────────────────────────────────────────
   Maia does not drive a browser, hold credentials, or run SQL. She calls
   deterministic backend tools and renders what they return.

     get_equipment_from_database → internal store (fast, no browser)
     search_equipment_in_sis     → Playwright automation against sis2.cat.com
     get_automation_run_status   → poll / explain a run
     get_equipment_history       → change history

   Grounding contract: every equipment fact rendered here comes from a tool
   result. `data` never exists without `attribution` (source + retrieved_at +
   run id). A failed lookup carries NO data object, so there is nothing to
   paraphrase into a confident-sounding answer.
   ══════════════════════════════════════════════════════════════════════════ */
const EQUIP=(()=>{
  const C=()=>(typeof CFG!=='undefined'&&CFG.equipment)||{};
  const results=new Map();     // token -> result, for card rendering
  const cache=new Map();       // serial -> {at, result}  (browser-side micro-cache)
  const inflight=new Map();    // serial -> promise, so one question = one lookup
  let current=null;            // most recent result, read by systemPrompt()

  const ASK_RE=/\b(serial|s\/?n|pin|equipment data|machine data|look\s?up|data for|details for|specs? for|دوريلي|دور على|هات(?:لي)?|بيانات|الداتا|سيريال|المعدة|معدة)\b/i;
  const REFRESH_RE=/\b(refresh|latest|newest|re-?check|update it|حدّث|حدث|اخر|آخر|اجدد|أجدد)\b/i;
  const SERIAL_RE=/^[A-Z0-9]{3,17}$/;

  // ── helpers ──────────────────────────────────────────────────────────────
  function norm(s){
    const AR='٠١٢٣٤٥٦٧٨٩', FA='۰۱۲۳۴۵۶۷۸۹';
    return String(s||'')
      .replace(/[٠-٩]/g,d=>String(AR.indexOf(d)))
      .replace(/[۰-۹]/g,d=>String(FA.indexOf(d)))
      .replace(/[\s\-_/.]/g,'').toUpperCase();
  }
  function esc(s){return (typeof escH==='function')?escH(s):String(s==null?'':s);}
  function wantsLookup(raw){return ASK_RE.test(raw||'');}
  function wantsRefresh(raw){return REFRESH_RE.test(raw||'');}
  function token(){return 'eq-'+Math.random().toString(36).slice(2,9);}

  function fmtDate(iso,L){
    if(!iso)return '—';
    const d=new Date(iso);
    if(isNaN(d))return String(iso).slice(0,19).replace('T',' ');
    return d.toLocaleString(L==='ar'?'ar-EG':'en-GB',
      {year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit'});
  }
  function ageLabel(days,L){
    if(days==null)return '';
    const d=Math.round(days);
    if(L==='ar')return d<1?'النهارده':(d===1?'من يوم':`من ${d} يوم`);
    return d<1?'today':(d===1?'1 day ago':`${d} days ago`);
  }

  // ── transport ────────────────────────────────────────────────────────────
  async function call(path,{method='GET',body=null,timeoutMs=null}={}){
    const base=C().apiBase;
    if(!base)return {ok:false,error_code:'NOT_CONFIGURED',
      user_message_hint:'The equipment data service is not configured in this build.'};
    const ctrl=new AbortController();
    const t=setTimeout(()=>ctrl.abort(),timeoutMs||C().timeoutMs||45000);
    try{
      const res=await fetch(base.replace(/\/$/,'')+path,{
        method,signal:ctrl.signal,
        headers:Object.assign({'Content-Type':'application/json'},
          C().actor?{'x-maya-actor':C().actor}:{},
          C().authToken?{'Authorization':'Bearer '+C().authToken}:{}),
        body:body?JSON.stringify(body):undefined
      });
      clearTimeout(t);
      const payload=await res.json().catch(()=>({}));
      return {httpStatus:res.status,payload};
    }catch(err){
      clearTimeout(t);
      const aborted=/abort/i.test(err.message||'');
      return {ok:false,error_code:aborted?'TIMEOUT':'NETWORK_ERROR',
        user_message_hint:aborted?'The equipment service did not answer in time.'
                                 :'The equipment service could not be reached.'};
    }
  }

  // ── the five tools ───────────────────────────────────────────────────────
  async function getFromDatabase(serial){
    const r=await call(`/v1/equipment/${encodeURIComponent(serial)}`,{timeoutMs:10000});
    if(r.error_code)return r;
    if(r.httpStatus===200)return {ok:true,found:true,...r.payload};
    if((r.payload||{}).error_code==='SERIAL_NOT_FOUND')
      return {ok:true,found:false,reason:'NOT_IN_STORE'};
    return {ok:false,error_code:(r.payload||{}).error_code||'INTERNAL_ERROR',
      user_message_hint:(r.payload||{}).user_message_hint||'Store lookup failed.'};
  }

  async function searchInSis(serial,reason,mode,opts){
    // wait:false → the gateway returns a run id immediately and we follow the
    // automation step by step, so the user sees it working instead of a spinner.
    const async_=(opts&&opts.async)!==false;
    const r=await call('/v1/equipment/search',{method:'POST',body:{
      serial_number:serial,source:'cat_sis',mode:mode||'auto',
      reason:reason||'user_request',wait:!async_,
      timeout_ms:(C().timeoutMs||45000)-5000}});
    if(r.error_code)return r;
    if(r.httpStatus===200)return {ok:true,found:true,...r.payload};
    if(r.httpStatus===202)return {ok:true,inProgress:true,...r.payload};
    const p=r.payload||{};
    return {ok:false,error_code:p.error_code||'INTERNAL_ERROR',
      user_message_hint:p.user_message_hint||p.message||'The SIS lookup failed.',
      automation_run_id:p.automation_run_id,fallback:p.fallback};
  }

  async function runStatus(runId){
    const r=await call(`/v1/runs/${encodeURIComponent(runId)}`,{timeoutMs:10000});
    if(r.error_code)return r;
    if(r.httpStatus!==200)return {ok:false,error_code:'INTERNAL_ERROR'};
    return {ok:true,...(r.payload.run||{})};
  }

  async function history(serial,limit){
    const r=await call(`/v1/equipment/${encodeURIComponent(serial)}/history?limit=${limit||10}`,
      {timeoutMs:15000});
    if(r.error_code)return r;
    if(r.httpStatus!==200)return {ok:false,error_code:'INTERNAL_ERROR'};
    return {ok:true,...r.payload};
  }

  // Human-readable names for the deterministic steps the worker executes.
  const STEP_LABEL={
    ACQUIRE_CONTEXT:{en:'Starting a browser session',ar:'بفتح جلسة متصفح'},
    ENSURE_SESSION :{en:'Signing in to Caterpillar SIS',ar:'بسجّل دخول على Caterpillar SIS'},
    HEALTH_CHECK   :{en:'Checking the SIS page contract',ar:'بتأكد إن صفحة SIS زي ما هي'},
    SEARCH_SERIAL  :{en:'Searching the serial number in SIS',ar:'بدوّر على السيريال في SIS'},
    EXTRACT_RAW    :{en:'Reading the equipment record',ar:'ببقرأ بيانات المعدة'},
    NORMALIZE      :{en:'Normalizing the data',ar:'بظبط شكل البيانات'},
    VALIDATE       :{en:'Validating the data',ar:'بتحقق من صحة البيانات'},
    PERSIST        :{en:'Saving to the internal store',ar:'بحفظ في الداتا بيز'}
  };
  function stepLabel(step,L){
    const e=STEP_LABEL[step];
    return e?(L==='ar'?e.ar:e.en):step.replace(/_/g,' ').toLowerCase();
  }

  // Poll a run and report every step as it happens, via onProgress.
  async function awaitRun(runId,audit,onProgress,L){
    const every=C().pollMs||2500, budget=C().maxPollMs||180000;
    const until=Date.now()+budget;
    let seen=0;
    while(Date.now()<until){
      await new Promise(r=>setTimeout(r,every));
      const st=await runStatus(runId);
      if(!st.ok)continue;
      const steps=st.steps_executed||[];
      for(;seen<steps.length;seen++){
        const s=steps[seen];
        audit.push({tool:'step:'+s.step,ok:s.status==='OK',error_code:s.error_code});
        onProgress&&onProgress({kind:'step',step:s.step,status:s.status,
          label:stepLabel(s.step,L),ms:s.duration_ms,error_code:s.error_code});
      }
      if(st.status==='AWAITING_HUMAN'){
        onProgress&&onProgress({kind:'awaiting_human',runId,
          label:L==='ar'?'محتاج تأكيد بشري على SIS (MFA)':'Waiting for human verification at SIS (MFA)'});
        continue;                       // the operator has to act; keep watching
      }
      if(st.status==='SUCCESS')return {done:true};
      if(st.status==='FAILED')
        return {done:true,failed:true,error_code:st.error_code||'INTERNAL_ERROR'};
    }
    return {done:false};
  }

  async function resumeRun(runId){
    const r=await call(`/v1/runs/${encodeURIComponent(runId)}/resume`,{method:'POST',timeoutMs:10000});
    return !r.error_code&&r.httpStatus===200;
  }

  // ── the decision flow (deterministic, client side of the same policy) ────
  async function lookup(serialRaw,opts={}){
    const progress=opts.onProgress||null, L=opts.lang||'en';
    const serial=norm(serialRaw);
    if(!SERIAL_RE.test(serial))
      return finish({ok:false,serial,error_code:'INVALID_SERIAL',
        hint:'A serial number is 3–17 letters/digits.',audit:[]});

    if(inflight.has(serial))return inflight.get(serial);        // de-duplicate

    const promise=(async()=>{
      const audit=[];
      progress&&progress({kind:'phase',label:L==='ar'?'بشوف الداتا بتاعتنا الأول':'Checking internal data…'});
      const ttl=(C().clientCacheMs||120000);
      const hit=cache.get(serial);
      if(hit&&!opts.force&&Date.now()-hit.at<ttl){
        audit.push({tool:'browser_cache',ok:true});
        return finish({...hit.result,audit:hit.result.audit.concat(audit)});
      }

      // 1. internal store first — always
      let db=null;
      if(!opts.force){
        db=await getFromDatabase(serial);
        audit.push({tool:'get_equipment_from_database',ok:!!db.ok,
          found:!!db.found,freshness:(db.attribution||{}).freshness,
          error_code:db.error_code});
        if(db.ok&&db.found){
          const fresh=(db.cache||{}).freshness||(db.attribution||{}).freshness;
          // Fresh, or stale-but-they-did-not-ask-for-a-refresh: serve the store.
          // Stale data is never presented unlabelled — the card and the reply
          // both carry its age, and Maia offers to refresh it.
          if(!opts.refresh)
            return finish({ok:true,serial,origin:'store',data:db.data,
              attribution:db.attribution,cacheInfo:db.cache,
              stale:(fresh!=='FRESH'),audit});
          audit.push({note:'refresh_requested',freshness:fresh});
        }
      }

      // 2. source automation
      progress&&progress({kind:'phase',label:L==='ar'
        ?'مش موجودة عندنا — بسأل Caterpillar SIS…'
        :'Not in internal data — querying Caterpillar SIS…'});
      const reason=(db&&db.found)?'stale_refresh':'user_request';
      let sis=await searchInSis(serial,reason,opts.force?'force_refresh':'auto');
      audit.push({tool:'search_equipment_in_sis',ok:!!sis.ok,reason,
        run:sis.automation_run_id||(sis.attribution||{}).automation_run_id,
        error_code:sis.error_code,ms:sis.execution_time_ms});

      if(sis.ok&&sis.inProgress){
        progress&&progress({kind:'phase',runId:sis.automation_run_id,
          label:(L==='ar'?'الأتمتة شغالة… Run ':'SIS automation running… Run ')+sis.automation_run_id});
        const polled=await awaitRun(sis.automation_run_id,audit,progress,L);
        if(polled.done&&!polled.failed){
          sis=await getFromDatabase(serial);
          audit.push({tool:'get_equipment_from_database',ok:!!sis.ok,after:'run_complete'});
        }else if(polled.done&&polled.failed){
          sis={ok:false,error_code:polled.error_code,
            automation_run_id:sis.automation_run_id,
            user_message_hint:'The automation run failed.'};
        }else{
          return finish({ok:false,serial,error_code:'TIMEOUT',runStillGoing:true,
            hint:'Still running. I can check again with the run id.',
            runId:sis.automation_run_id,audit});
        }
      }

      if(sis.ok&&sis.data)
        return finish({ok:true,serial,origin:'sis',data:sis.data,
          attribution:sis.attribution,cacheInfo:sis.cache,
          executionMs:sis.execution_time_ms,audit});

      // 3. source failed — offer the stored copy, always labelled with its age
      const staleCopy=(db&&db.found&&db.data)?{data:db.data,attribution:db.attribution,
        ageDays:(db.cache||{}).age_days}:
        (sis.fallback&&sis.fallback.available?{data:sis.fallback.data,
          attribution:sis.fallback.attribution,ageDays:sis.fallback.age_days}:null);

      return finish({ok:false,serial,error_code:sis.error_code||'INTERNAL_ERROR',
        hint:sis.user_message_hint,runId:sis.automation_run_id,stale:staleCopy,audit});
    })();

    inflight.set(serial,promise);
    try{return await promise;}finally{inflight.delete(serial);}
  }

  function finish(result){
    result.token=token();
    results.set(result.token,result);
    if(result.ok)cache.set(result.serial,{at:Date.now(),result});
    current=result;
    return result;
  }

  // Runs before the model, so the model is grounded rather than guessing.
  async function maybeLookup(raw,ents,cls,L,opts){
    if(!C().enabled)return null;
    const serial=ents&&ents.serial?ents.serial:null;
    if(!serial)return null;
    const intentMatches=(cls&&cls.intent==='equipment_lookup')||wantsLookup(raw);
    if(!intentMatches)return null;
    return lookup(serial,{refresh:wantsRefresh(raw),force:wantsRefresh(raw),
                          lang:L,onProgress:opts&&opts.onProgress});
  }

  // ── grounding for the LLM ────────────────────────────────────────────────
  function facts(r){
    if(!r||!r.ok)return [];
    const d=r.data||{}, a=r.attribution||{};
    const out=[
      `EQUIPMENT ${d.serial_number||r.serial} · model=${d.equipment_model??'null'}`,
      `type=${d.equipment_type??'null'} · build_date=${d.build_date??'null'} · manufacturer=${d.manufacturer??'null'}`,
      `engine=${(d.engine_family&&d.engine_family.model)??'null'} · emissions=${(d.engine_family&&d.engine_family.emissions)??'null'}`,
      `source=${a.source_label||a.source} · retrieved_at=${a.retrieved_at} · run_id=${a.automation_run_id} · freshness=${a.freshness} · age_days=${a.age_days??0}`
    ];
    (d.specifications||[]).slice(0,10).forEach(s=>out.push(
      `spec ${s.group?s.group+' / ':''}${s.name} = ${s.value_raw||s.value||'null'}${s.unit&&!s.value_raw?' '+s.unit:''}`));
    if(d.parts_manual_url)out.push(`parts_manual_url=${d.parts_manual_url}`);
    if(d.operation_manual_url===null)out.push('operation_manual_url=null (NOT PUBLISHED by the source)');
    const viol=((d.quality||{}).violations)||[];
    if(viol.length)out.push(`unreadable_this_run=${viol.join(', ')}`);
    return out;
  }

  function injectDocs(r,docs){
    if(!Array.isArray(docs))return;
    facts(r).forEach((line,i)=>docs.unshift({id:'SIS '+(r.serial||''),line,score:1}));
  }

  // Injected into the system prompt. Binds the model to the record or to the failure.
  function promptBlock(){
    const r=current;
    if(!r)return '';
    if(r.ok){
      return `EQUIPMENT RECORD (authoritative — retrieved by tool this turn)
${facts(r).map(f=>'• '+f).join('\n')}

RULES FOR THIS RECORD
- Use ONLY these values. Never add a model, date, engine, spec or URL that is not listed above.
- A value shown as null is NOT published by the source — say that, do not guess it.
- State the source, the retrieval timestamp and the Automation Run ID in your reply.
- If freshness is STALE or HARD_STALE, say how old the data is before presenting it.`;
    }
    return `EQUIPMENT LOOKUP FAILED (no data was returned — there is nothing to present)
• serial=${r.serial} · error_code=${r.error_code} · run_id=${r.runId||'n/a'}
• detail: ${r.hint||''}
${r.stale?`• a stored copy exists, ${Math.round(r.stale.ageDays||0)} days old — you may offer it, but you MUST state its age and that it is not current.`:'• no stored copy exists.'}

RULES FOR THIS FAILURE
- Tell the customer plainly that the lookup failed and why, and give the Run ID.
- Do NOT state any equipment attribute for this serial. You do not have one.`;
  }

  // ── deterministic reply (used when the model is unreachable) ─────────────
  function composeReply(r,L){
    const ar=L==='ar';
    if(!r)return '';
    if(r.ok){
      const d=r.data||{}, a=r.attribution||{};
      const fromStore=r.origin==='store';
      const head=ar
        ?(fromStore?`لقيت بيانات المعدة **${r.serial}** في الـ internal data store.`
                   :`لقيت بيانات المعدة **${r.serial}** من Caterpillar SIS.`)
        :(fromStore?`Found **${r.serial}** in the internal data store.`
                   :`Retrieved **${r.serial}** from Caterpillar SIS.`);
      const rows=[
        [ar?'الموديل':'Model',d.equipment_model],
        [ar?'النوع':'Type',d.equipment_type],
        [ar?'تاريخ التصنيع':'Build date',d.build_date],
        [ar?'المحرك':'Engine',d.engine_family&&d.engine_family.model]
      ].filter(x=>x[1]).map(x=>`• ${x[0]}: **${x[1]}**`).join('\n');
      const src=`${ar?'المصدر':'Source'}: ${a.source_label||'—'} · ${ar?'وقت السحب':'Retrieved'}: ${fmtDate(a.retrieved_at,L)}`
        +(a.age_days?` (${ageLabel(a.age_days,L)})`:'')+` · Run ID: ${a.automation_run_id||'—'}`;
      const staleNote=(a.freshness==='STALE'||a.freshness==='HARD_STALE')
        ?(ar?`\n\n⚠️ البيانات دي عمرها ${Math.round(a.age_days||0)} يوم — تحب أحدّثها من SIS؟`
            :`\n\n⚠️ This copy is ${Math.round(a.age_days||0)} days old — want me to refresh it from SIS?`):'';
      return `${head}\n${rows}\n\n${src}${staleNote}`;
    }
    const why=({
      SERIAL_NOT_FOUND: ar?'السيريال ده مش موجود في SIS':'that serial has no record in SIS',
      INVALID_SERIAL:   ar?'شكل السيريال مش مظبوط':'the serial number format is not valid',
      TIMEOUT:          ar?'SIS مردش في الوقت المحدد':'SIS did not answer in time',
      NETWORK_ERROR:    ar?'مقدرتش أوصل لـ SIS':'SIS could not be reached',
      LOGIN_FAILED:     ar?'الدخول على SIS فشل':'sign-in to SIS failed',
      CAPTCHA_DETECTED: ar?'فيه تحقق أمني محتاج تدخل بشري':'a security challenge needs a human',
      WEBSITE_CHANGED:  ar?'صفحة SIS اتغيرت وفريق الهندسة اتبلّغ':'the SIS page changed; engineering was alerted',
      RATE_LIMITED:     ar?'عدد الطلبات كتير دلوقتي':'too many lookups right now',
      CIRCUIT_OPEN:     ar?'البحث في SIS متوقف مؤقتاً بعد أعطال متكررة':'SIS lookups are paused after repeated failures',
      NOT_CONFIGURED:   ar?'خدمة بيانات المعدات مش متوصلة في النسخة دي':'the equipment data service is not connected in this build'
    })[r.error_code]||(ar?'حصل خطأ غير متوقع':'an unexpected error occurred');
    let msg=ar?`مقدرتش أجيب بيانات **${r.serial}** — ${why}.`
              :`I couldn't retrieve **${r.serial}** — ${why}.`;
    if(r.runId)msg+=`\nRun ID: ${r.runId}`;
    if(r.stale)msg+=ar?`\n\nعندي نسخة محفوظة عمرها ${Math.round(r.stale.ageDays||0)} يوم — أعرضها؟`
                     :`\n\nI have a stored copy ${Math.round(r.stale.ageDays||0)} days old — show it?`;
    return msg;
  }

  // Layer attribution onto whatever the model wrote; never replace its voice.
  function mergeAnswer(out,r,L){
    if(!out||!r)return out;
    const ar=L==='ar';
    if(r.ok){
      const a=r.attribution||{};
      const stamp=`${ar?'المصدر':'Source'}: ${a.source_label||'—'} · ${ar?'وقت السحب':'Retrieved'}: ${fmtDate(a.retrieved_at,L)} · Run ID: ${a.automation_run_id||'—'}`;
      if(!out.reply||!/Run ID/i.test(out.reply))
        out.reply=(out.reply||composeReply(r,L))+`\n\n${stamp}`;
      out.sources=(out.sources||[]).concat([`${a.source_label||'SIS'} · ${r.serial}`]);
      out.confidence=Math.max(out.confidence||0,0.95);
      if(typeof SESSION!=='undefined'&&r.data){
        if(r.data.equipment_model&&!SESSION.slots.machine)
          SESSION.slots.machine='CAT '+r.data.equipment_model;
        SESSION.slots.serial=r.serial;
        if(typeof renderContextStrip==='function')renderContextStrip();
      }
      if(!(out.quick_replies||[]).length)
        out.quick_replies=ar?['قطع الغيار المناسبة','جدول الصيانة','حدّث من SIS','افتح تذكرة']
                            :['Matching parts','PM schedule','Refresh from SIS','Open a ticket'];
    }else{
      // The model has no data for this serial; make sure the reply says so.
      out.reply=composeReply(r,L);
      out.confidence=0.9;                       // confident about the failure itself
      out.quick_replies=ar?['جرّب تاني','افتح تذكرة','اتكلم مع مهندس']
                          :['Try again','Open a ticket','Talk to an engineer'];
    }
    out.intent='equipment_lookup';
    return out;
  }

  // ── live run panel ───────────────────────────────────────────────────────
  // Inserted into the chat as soon as a lookup starts, updated on every step, and
  // left in place as the audit trail of what the automation actually did.
  function openLivePanel(serial,L){
    const id='eqlive-'+Math.random().toString(36).slice(2,8);
    const host=document.getElementById('chatMessages');
    if(!host)return {id,update(){},close(){}};
    const wrap=document.createElement('div');
    wrap.className='msg';
    wrap.innerHTML=`<div class="msg-av ai">${typeof AI_SVG!=='undefined'?AI_SVG:''}</div>
      <div style="flex:1;min-width:0;"><div class="eq-live" id="${id}">
        <div class="eq-live-h"><span class="eq-live-dot"></span>
          <b>${L==='ar'?'بشتغل على':'Working on'} ${esc(serial)}</b>
          <span class="eq-live-run" id="${id}-run"></span></div>
        <div class="eq-live-body" id="${id}-body"></div>
      </div></div>`;
    host.appendChild(wrap);
    if(typeof scrollBottom==='function')scrollBottom();

    const body=()=>document.getElementById(id+'-body');
    return {
      id,
      update(ev){
        const b=body(); if(!b)return;
        if(ev.runId){
          const r=document.getElementById(id+'-run');
          if(r)r.textContent='Run '+ev.runId;
        }
        if(ev.kind==='awaiting_human'){
          b.insertAdjacentHTML('beforeend',
            `<div class="eq-live-row human"><span>⏸ ${esc(ev.label)}</span>
               <button class="eq-btn" data-act="eq-resume" data-run="${esc(ev.runId||'')}">
                 ${L==='ar'?'خلّصت — كمّل':'I have completed it — continue'}</button></div>`);
        }else{
          const icon=ev.kind==='step'?(ev.status==='OK'?'✓':'✗'):'•';
          const cls=ev.kind==='step'&&ev.status!=='OK'?' fail':'';
          const ms=ev.ms!=null?`<span class="eq-live-ms">${ev.ms} ms</span>`:'';
          b.insertAdjacentHTML('beforeend',
            `<div class="eq-live-row${cls}"><span>${icon} ${esc(ev.label)}</span>${ms}</div>`);
        }
        if(typeof scrollBottom==='function')scrollBottom();
      },
      close(ok){
        const el=document.getElementById(id);
        if(el)el.classList.add(ok?'done':'failed');
      }
    };
  }

  // ── rendering ────────────────────────────────────────────────────────────
  function cardFor(tok,L){
    const r=results.get(tok);
    return r?renderCard(r,L):'';
  }

  function renderCard(r,L){
    const ar=L==='ar';
    if(!r.ok)return renderFailure(r,L);
    const d=r.data||{}, a=r.attribution||{};
    const fresh=(a.freshness||'FRESH');
    const badge=fresh==='FRESH'
      ?`<span class="eq-badge ok">${ar?'محدّثة':'Fresh'}</span>`
      :`<span class="eq-badge warn">${ar?'قديمة':'Stale'} · ${ageLabel(a.age_days,L)}</span>`;
    const originBadge=r.origin==='store'
      ?`<span class="eq-badge store">${ar?'من الداتا بيز':'Internal store'}</span>`
      :`<span class="eq-badge live">${ar?'سحب مباشر من SIS':'Live from SIS'}</span>`;

    const row=(label,value,note)=>{
      if(value===undefined)return '';
      const missing=(value===null||value==='');
      return `<div class="eq-row"><span class="eq-k">${esc(label)}</span>
        <span class="eq-v${missing?' missing':''}">${missing
          ?(note||(ar?'مش منشور في SIS':'not published by SIS'))
          :esc(value)}</span></div>`;
    };

    const specs=(d.specifications||[]).slice(0,8).map(s=>
      `<div class="eq-spec"><span>${esc((s.group?s.group+' · ':'')+s.name)}</span>
        <b>${esc(s.value_raw||((s.value??'')+(s.unit?' '+s.unit:'')))}</b></div>`).join('');

    const viol=((d.quality||{}).violations||[]).filter(v=>!v.startsWith('empty_spec'));
    const unread=viol.length?`<div class="eq-warn">${ar?'حقول مقدرتش أقراها في السحبة دي':'Fields I could not read this run'}: ${esc(viol.join(', '))}</div>`:'';

    const links=[
      d.parts_manual_url?`<a class="eq-link" href="${esc(d.parts_manual_url)}" target="_blank" rel="noopener">${ar?'كتالوج القطع':'Parts manual'} ↗</a>`:'',
      d.source_url?`<a class="eq-link" href="${esc(d.source_url)}" target="_blank" rel="noopener">${ar?'المصدر في SIS':'Open in SIS'} ↗</a>`:''
    ].join('');

    return `<div class="eq-card">
      <div class="eq-head">
        <div><div class="eq-serial">${esc(d.serial_number||r.serial)}</div>
          <div class="eq-sub">${esc(d.manufacturer||'Caterpillar')} ${esc(d.equipment_model||'')}</div></div>
        <div class="eq-badges">${originBadge}${badge}</div>
      </div>
      <div class="eq-body">
        ${row(ar?'الموديل':'Model',d.equipment_model??null)}
        ${row(ar?'النوع':'Type',d.equipment_type??null)}
        ${row(ar?'تاريخ التصنيع':'Build date',d.build_date??null)}
        ${row(ar?'المحرك':'Engine',(d.engine_family&&d.engine_family.model)??null)}
        ${d.engine_family&&d.engine_family.emissions?row(ar?'معيار الانبعاثات':'Emissions',d.engine_family.emissions):''}
        ${d.operation_manual_url===null?row(ar?'دليل التشغيل':'Operation manual',null):''}
        ${d.parts_manual_url===null?row(ar?'كتالوج القطع':'Parts manual',null):''}
        ${specs?`<div class="eq-specs-h">${ar?'المواصفات':'Specifications'}</div>${specs}`:''}
        ${unread}
        ${links?`<div class="eq-links">${links}</div>`:''}
      </div>
      <div class="eq-foot">
        <span>${ar?'المصدر':'Source'}: <b>${esc(a.source_label||'—')}</b></span>
        <span>${ar?'وقت السحب':'Retrieved'}: <b>${esc(fmtDate(a.retrieved_at,L))}</b></span>
        <span>Run ID: <b>${esc(a.automation_run_id||'—')}</b></span>
        ${(d.quality&&d.quality.score!=null)?`<span>${ar?'جودة':'Quality'}: <b>${Math.round(d.quality.score*100)}%</b></span>`:''}
      </div>
      <div class="eq-actions">
        <button class="eq-btn" data-act="ask" data-q="${ar?'حدّث بيانات '+esc(r.serial)+' من SIS':'Refresh '+esc(r.serial)+' from SIS'}">${ar?'تحديث من SIS':'Refresh from SIS'}</button>
        <button class="eq-btn ghost" data-act="ask" data-q="${ar?'تاريخ التغييرات لـ '+esc(r.serial):'Change history for '+esc(r.serial)}">${ar?'تاريخ التغييرات':'Change history'}</button>
      </div>
    </div>`;
  }

  function renderFailure(r,L){
    const ar=L==='ar';
    const stale=r.stale?`<div class="eq-fallback">
        <b>${ar?'نسخة محفوظة':'Stored copy'}</b> — ${ar?'عمرها':'age'} ${Math.round(r.stale.ageDays||0)} ${ar?'يوم':'days'}.
        ${ar?'دي مش البيانات الحالية.':'This is not current data.'}
        <div class="eq-row"><span class="eq-k">${ar?'الموديل':'Model'}</span>
          <span class="eq-v">${esc((r.stale.data||{}).equipment_model||'—')}</span></div>
      </div>`:'';
    return `<div class="eq-card fail">
      <div class="eq-head">
        <div><div class="eq-serial">${esc(r.serial||'—')}</div>
          <div class="eq-sub">${ar?'فشل سحب البيانات':'Lookup failed'}</div></div>
        <div class="eq-badges"><span class="eq-badge err">${esc(r.error_code||'ERROR')}</span></div>
      </div>
      <div class="eq-body">
        <div class="eq-warn">${esc(r.hint||(ar?'مفيش بيانات اترجعت.':'No data was returned.'))}</div>
        ${ar?'<div class="eq-note">مفيش أي بيانات للمعدة دي في الرد ده — مش هعرض تخمين.</div>'
            :'<div class="eq-note">No equipment values were returned, so none are shown. Nothing here is guessed.</div>'}
        ${stale}
      </div>
      <div class="eq-foot"><span>Run ID: <b>${esc(r.runId||'—')}</b></span></div>
      <div class="eq-actions">
        <button class="eq-btn" data-act="ask" data-q="${ar?'جرّب تاني '+esc(r.serial||''):'Try '+esc(r.serial||'')+' again'}">${ar?'جرّب تاني':'Try again'}</button>
        <button class="eq-btn ghost" data-act="ask" data-q="${ar?'افتح تذكرة':'Open a ticket'}">${ar?'افتح تذكرة':'Open a ticket'}</button>
      </div>
    </div>`;
  }

  // Extra rows for the existing "How I got this" panel.
  function traceRows(t,row){
    if(!t||!t.audit)return '';
    const steps=t.audit.map(a=>{
      const name=a.tool||a.note||'step';
      const state=a.ok===false?'<span class="no">failed</span>'
                 :a.error_code?`<span class="no">${esc(a.error_code)}</span>`
                 :'<span class="ok">ok</span>';
      return `${esc(name)} ${state}`;
    }).join(' → ');
    return row('tools',steps)
      +(t.runId?row('run id',`<span class="hl">${esc(t.runId)}</span>`):'')
      +(t.source?row('data source',`<span class="hl">${esc(t.source)}</span> · ${esc(t.freshness||'')}`):'');
  }

  function traceOf(r){
    if(!r)return null;
    const a=r.attribution||{};
    return {audit:r.audit||[],runId:a.automation_run_id||r.runId,
      source:a.source_label||(r.ok?'unknown':null),freshness:a.freshness,
      origin:r.origin,error_code:r.error_code};
  }

  return {wantsLookup,wantsRefresh,norm,lookup,maybeLookup,injectDocs,promptBlock,
          resumeRun,stepLabel,openLivePanel,
          composeReply,mergeAnswer,cardFor,renderCard,traceRows,traceOf,facts,
          getFromDatabase,searchInSis,runStatus,history,
          get current(){return current;}};
})();
