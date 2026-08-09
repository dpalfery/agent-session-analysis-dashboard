/* app.js -- Agent Session Analysis UI.
 *
 * Served-only: all data comes from serve.py's JSON API at runtime. This file
 * and dashboard.html contain no captured telemetry, which is why they can be
 * tracked in git. Opening dashboard.html from file:// will not work.
 *
 * `D` is the global context (rates, tokenizer, attribute coverage, orphan
 * spend) fetched once from /api/meta; per-session payloads come from
 * /api/session/<id>. The view functions below are unchanged from the previous
 * build-time renderer -- only the data source moved.
 */
let D = {rates:{}, tokenizer:{}, harnesses:{}, sources:[]};
let HMETA = {};      // meta for the harness of the session on screen
let SESSIONS = [];
let CUR = null;
let CUR_SESSION = null;   // full payload of the session on screen
let SPAN_MAP = {};         // spanId → raw_attributes (from timeline walk)
let CTXDATA = [];          // context chart click data [{rowIdx,label,key,value,total}]

const fmt  = n => n===null||n===undefined ? null : n.toLocaleString();
const kt   = n => n===0 ? '0' : (n/1000).toFixed(n>=10000?0:1)+'k';
const pct  = n => n===null||n===undefined ? null : (n*100).toFixed(1)+'%';
const esc  = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dur  = ms => ms===null||ms===undefined ? null
  : ms<1000 ? ms+'ms' : ms<60000 ? (ms/1000).toFixed(1)+'s'
  : Math.floor(ms/60000)+'m '+Math.round(ms%60000/1000)+'s';
// An absent value renders as an explicit marker. Never as 0.
const cell = v => v===null||v===undefined ? '<span class="absent">not recorded</span>' : v;
const fmtCredits = c => c===null||c===undefined ? null : c>=100 ? c.toFixed(0) : c.toFixed(2);
const usd = v => v===null||v===undefined ? null : '$'+(Math.abs(v)<0.01&&v!==0 ? v.toFixed(4) : v.toFixed(2));


/* Skill-name coverage, read from the harness meta rather than hardcoding an
   attribute key -- the key differs per harness and this file must stay
   harness-neutral. */
function skillCoverage(){
  const pa = HMETA.partial_attributes || {};
  const k = Object.keys(pa).find(x=>x.toLowerCase().includes('skill_name'));
  return k ? pa[k] : null;
}

/* ---------------- side drawer ---------------- */
function buildSpanMap(nodes){
  SPAN_MAP = {};
  (function walk(ns){ ns.forEach(n=>{ SPAN_MAP[n.spanId]=n; walk(n.children||[]); }); })(nodes||[]);
}
function openDrawer(title, html){
  document.getElementById('drw-title').textContent = title;
  document.getElementById('drw-body').innerHTML = html;
  document.getElementById('drw').classList.add('open');
  document.getElementById('drw-backdrop').classList.add('open');
  document.getElementById('drw-body').scrollTop = 0;
}
function closeDrawer(){
  document.getElementById('drw').classList.remove('open');
  document.getElementById('drw-backdrop').classList.remove('open');
}
document.addEventListener('keydown', e=>{ if(e.key==='Escape') closeDrawer(); });

// JSON attribute values may be truncated server-side; parse defensively.
function tryJSON(v){
  if(v===null||v===undefined) return null;
  if(typeof v!=='string') return v;
  try{ return JSON.parse(v); }catch(e){ return null; }
}

// Harness-injected XML blocks folded into <details>; everything else escaped.
const XML_FOLD_TAGS = ['environment_info','workspace_info','instructions',
  'copilot_instructions','context','reminderInstructions','editor_context',
  'tool_use_instructions','notebook_info','system_reminder','file_contents',
  'attachment','environment_details'];
function fmtText(raw){
  if(!raw) return '';
  const re = new RegExp('<('+XML_FOLD_TAGS.join('|')+')(\\s[^>]*)?>([\\s\\S]*?)(?:</\\1>|$)','g');
  let html='', last=0, m;
  while((m=re.exec(raw))!==null){
    const before=raw.slice(last,m.index).trim();
    if(before) html+=`<p class="dr-p">${esc(before)}</p>`;
    const inner=m[3].trim();
    const preview=inner.slice(0,90).replace(/\s+/g,' ');
    html+=`<details class="dr-xml"><summary><span class="dr-xml-tag">&lt;${esc(m[1])}&gt;</span>
      <span class="dr-xml-prev">${esc(preview)}${inner.length>90?'…':''}</span></summary>
      <pre class="dr-pre">${esc(inner)}</pre></details>`;
    last=m.index+m[0].length;
  }
  const rest=raw.slice(last).trim();
  if(rest) html+=`<p class="dr-p">${esc(rest)}</p>`;
  return html;
}

// One raw Copilot / canonical message part → readable HTML.
function fmtPart(p){
  if(!p || typeof p!=='object') return '';
  const t=p.type;
  if(t==='text') return fmtText(p.content!==undefined?p.content:(p.text||''));
  if(t==='reasoning'){
    const r=p.content||p.text||(p.raw&&(p.raw.content||p.raw.text))||JSON.stringify(p.raw||p);
    return `<details class="dr-xml"><summary><span class="dr-xml-tag">reasoning</span></summary>
      <pre class="dr-pre">${esc(typeof r==='string'?r:JSON.stringify(r,null,2))}</pre></details>`;
  }
  if(t==='tool_call'||(p.name&&p.arguments!==undefined)){
    const src=p.raw||p;
    let args=src.arguments!==undefined?src.arguments:src.input;
    const parsed=typeof args==='string'?(tryJSON(args)??args):args;
    return `<details class="dr-tool"><summary><span class="dr-tc-ico">🔧</span>
      <span class="dr-tc-name">${esc(src.name||'tool call')}</span></summary>
      ${kvTable(parsed)}</details>`;
  }
  if(t==='tool_result'||t==='tool_call_response'||t==='tool_call_result'){
    const src=p.raw!==undefined?p.raw:(p.response!==undefined?p.response:p);
    return `<details class="dr-tool res"><summary><span class="dr-tc-ico">↩</span>
      <span class="dr-tc-name">tool result</span></summary>${kvTable(src)}</details>`;
  }
  return `<details class="dr-xml"><summary><span class="dr-xml-tag">${esc(t||'part')}</span></summary>
    <pre class="dr-pre">${esc(JSON.stringify(p,null,2))}</pre></details>`;
}

// Object → readable key/value list; strings render as folded text, not JSON.
function kvTable(v){
  if(v===null||v===undefined) return '<p class="dr-p dr-dim">empty</p>';
  if(typeof v==='string') return fmtText(v)||`<p class="dr-p dr-dim">empty</p>`;
  if(Array.isArray(v)) return v.map(kvTable).join('');
  if(typeof v!=='object') return `<p class="dr-p">${esc(String(v))}</p>`;
  const rows=Object.entries(v).map(([k,val])=>{
    const body = typeof val==='string'
      ? (val.length>200?`<details class="dr-xml"><summary><span class="dr-xml-prev">${esc(val.slice(0,90).replace(/\s+/g,' '))}…</span></summary><pre class="dr-pre">${esc(val)}</pre></details>`:esc(val))
      : `<span class="mono">${esc(JSON.stringify(val))}</span>`;
    return `<div class="dr-kv"><span class="dr-k">${esc(k)}</span><div class="dr-vv">${body}</div></div>`;
  });
  return rows.join('');
}

function fmtMessages(msgs, opts){
  const only=(opts||{}).roles;
  return (msgs||[]).filter(m=>!only||only.includes(m.role)).map(m=>{
    const cls=m.role==='user'?'usr':m.role==='assistant'?'ast':'sys';
    const parts=(m.parts||[]).map(fmtPart).join('') ||
      (typeof m.content==='string'?fmtText(m.content):'');
    if(!parts) return '';
    return `<div class="dr-msg ${cls}"><div class="dr-role">${esc(m.role||'?')}</div>${parts}</div>`;
  }).join('');
}

function statRow(label, val, extra){
  return `<div class="dr-stat"><span class="dr-k">${esc(label)}</span>
    <span class="dr-sv">${val}</span>${extra?`<span class="dr-dim">${extra}</span>`:''}</div>`;
}

/* -- turn drawer -- */
function drawerTurn(i){
  const s=CUR_SESSION; if(!s) return;
  const t=s.turns[i]; if(!t) return;
  const segs=[['Cache-read input',t.cache_read,'var(--cache)'],
              ['Cache-creation input',t.cache_creation,'#4d8fc9'],
              ['Fresh input',t.fresh,'var(--fresh)'],
              ['Output',t.visible_output,'var(--out)'],
              ['Reasoning output',t.reasoning,'var(--reason)']].filter(([,v])=>v);
  const total=segs.reduce((a,[,v])=>a+v,0)||1;
  const bar = `<div class="dr-minibar">${segs.map(([l,v,c])=>
    `<span style="width:${(v/total*100).toFixed(2)}%;background:${c}" title="${l}: ${fmt(v)}"></span>`).join('')}</div>`;
  const rows=segs.map(([l,v,c])=>
    `<div class="dr-stat"><i class="dr-dot" style="background:${c}"></i>
     <span class="dr-k">${l}</span><span class="dr-sv">${fmt(v)}</span>
     <span class="dr-dim">${(v/total*100).toFixed(1)}%</span></div>`).join('');

  let html=`<div class="dr-sec"><div class="dr-shead">Token spend — ${fmt(total)} total</div>${bar}${rows}
    ${statRow('Model', esc(t.model||'—'))}
    ${t.durationMs!==null&&t.durationMs!==undefined?statRow('Duration',dur(t.durationMs)):''}
    ${t.ttft_ms!==null&&t.ttft_ms!==undefined?statRow('Time to first token',Math.round(t.ttft_ms)+'ms'):''}
    ${t.credits!==null&&t.credits!==undefined?statRow('Cost',fmtCredits(t.credits)+' credits'+(t.usd!==null&&t.usd!==undefined?' · '+usd(t.usd):'')):''}
  </div>`;
  if(t.fresh_jump_pct!==null)
    html+=`<div class="note bad dr-note">Fresh input jumped <strong>+${t.fresh_jump_pct}%</strong> vs the previous turn — cache miss or new material pulled in.</div>`;
  if(t.request_start)
    html+=`<div class="note dr-note">New request starts at this turn: <em>${esc(t.request_start)}</em></div>`;

  const attrs=(SPAN_MAP[t.spanId]||{}).attributes||{};
  const inMsgs=tryJSON(attrs['gen_ai.input.messages']);
  const outMsgs=tryJSON(attrs['gen_ai.output.messages']);
  if(inMsgs){
    const users=inMsgs.filter(m=>m.role==='user');
    const lastUser=users.length?[users[users.length-1]]:[];
    const conv=fmtMessages(lastUser);
    if(conv) html+=`<div class="dr-sec"><div class="dr-shead">Latest user message</div>${conv}</div>`;
  }
  if(outMsgs){
    const conv=fmtMessages(outMsgs);
    if(conv) html+=`<div class="dr-sec"><div class="dr-shead">Model response</div>${conv}</div>`;
  }
  if(!inMsgs&&!outMsgs){
    const has=Object.keys(attrs).length;
    html+= has
      ? `<div class="dr-sec"><div class="dr-shead">Span attributes</div>${kvTable(attrs)}</div>`
      : `<div class="note dr-note">No message content was recorded on this span${
          attrs['gen_ai.input.messages']?' (it was truncated during export)':''}.</div>`;
  }
  openDrawer(`Turn ${t.index}`, html);
}

/* -- context bucket drawer -- */
function drawerCtx(ci){
  const d=CTXDATA[ci]; if(!d||!CUR_SESSION) return;
  let html=`<div class="dr-sec"><div class="dr-shead">${esc(d.label)}</div>
    ${statRow('Tokens (approx.)',fmt(d.value))}
    ${statRow('Share of reported input',(d.value/d.total*100).toFixed(1)+'%')}
    ${statRow('Reported input total',fmt(d.total))}</div>`;

  // Pull the matching content off the chat span that carried input_messages.
  const ctx=CUR_SESSION.context||{};
  const src=d.rowIdx===0?ctx.first:ctx.last;
  const turn=src?CUR_SESSION.turns[(src.turn||1)-1]:null;
  const attrs=turn?((SPAN_MAP[turn.spanId]||{}).attributes||{}):{};
  if(d.key==='System prompt'){
    const sys=attrs['gen_ai.system_instructions'];
    if(sys) html+=`<div class="dr-sec"><div class="dr-shead">System prompt text</div>${fmtText(sys)}</div>`;
  }else if(d.key==='built-in tools'||d.key.startsWith('mcp: ')){
    const defs=tryJSON(attrs['gen_ai.tool.definitions']);
    if(defs){
      const mine=defs.filter(td=>{
        const n=(td.name||(td.function||{}).name||'');
        const isMcp=n.startsWith('mcp_');
        return d.key==='built-in tools'?!isMcp:isMcp&&n.startsWith('mcp_'+d.key.slice(5));
      });
      if(mine.length) html+=`<div class="dr-sec"><div class="dr-shead">${mine.length} tool definition(s)</div>
        ${mine.map(td=>{const f=td.function||td;return `<details class="dr-tool"><summary><span class="dr-tc-ico">🔧</span>
          <span class="dr-tc-name">${esc(td.name||f.name||'?')}</span></summary>
          ${f.description?`<p class="dr-p">${esc(f.description)}</p>`:''}
          ${f.parameters?kvTable(f.parameters):''}</details>`;}).join('')}</div>`;
    }
  }else if(d.key==='Conversation history'||d.key==='File contents via tool results'){
    const msgs=tryJSON(attrs['gen_ai.input.messages']);
    if(msgs){
      const conv = d.key==='Conversation history'
        ? fmtMessages(msgs.map(m=>({...m,parts:(m.parts||[]).filter(p=>p.type!=='tool_result'&&p.type!=='tool_call_response')})))
        : msgs.map(m=>(m.parts||[]).filter(p=>['tool_result','tool_call_response','tool_call_result'].includes(p.type)).map(fmtPart).join('')).join('');
      if(conv) html+=`<div class="dr-sec"><div class="dr-shead">${esc(d.key)}</div>${conv}</div>`;
    }
  }else if(d.key.startsWith('Unattributed')){
    html+=`<div class="note dr-note">This band is the gap between the sum of measured buckets and the model's own
      reported <span class="mono">input_tokens</span> — chat framing, role delimiters and tokenizer drift.
      It cannot be attributed to specific content.</div>`;
  }
  openDrawer(d.key, html);
}

/* -- tool drawer -- */
function drawerTool(i){
  const s=CUR_SESSION; if(!s) return;
  const t=s.tools.filter(x=>x.in_definitions)[i]; if(!t) return;
  const dead=t.invocations===0;
  let html=`${dead?'<div class="note bad dr-note"><strong>Never invoked.</strong> Its schema still costs tokens on every turn it is offered.</div>':''}
    <div class="dr-sec"><div class="dr-shead">Cost</div>
    ${statRow('Schema tokens',fmt(t.schema_tokens))}
    ${statRow('Turns resident',t.turns_resident)}
    ${statRow('Total schema cost',fmt(t.total_schema_cost),'schema × turns')}
    ${statRow('Invocations',t.invocations)}
    ${t.cost_per_invocation!==null?statRow('Cost per invocation',fmt(t.cost_per_invocation)):''}
    ${t.result_tokens!==null&&t.result_tokens!==undefined?statRow('Result tokens',fmt(t.result_tokens)):''}
    ${statRow('Server',esc(t.server)+(t.is_mcp?' (MCP)':''))}</div>`;
  // Schema definition off the first chat span that carried tool definitions.
  const turn=(s.turns||[]).find(x=>x.has_tool_defs);
  const attrs=turn?((SPAN_MAP[turn.spanId]||{}).attributes||{}):{};
  const defs=tryJSON(attrs['gen_ai.tool.definitions']);
  const def=defs&&defs.find(td=>(td.name||(td.function||{}).name)===t.name);
  if(def){
    const f=def.function||def;
    html+=`<div class="dr-sec"><div class="dr-shead">Schema definition</div>
      ${f.description?`<p class="dr-p">${esc(f.description)}</p>`:''}
      ${f.parameters?`<div class="dr-shead" style="margin-top:10px">Parameters</div>${kvTable(f.parameters)}`:''}</div>`;
  }
  openDrawer(t.name, html);
}

/* ---------------- cost ---------------- */
// Rates come from rates.json and are never inferred. A model with no published
// rate renders as "no published rate", never as $0.00.
function costView(s){
  const c=s.summary.cost;
  if(!c) return '';
  const R=D.rates||{};
  const unpriced = c.status==='no_rate';
  const rows=c.by_model.map(m=>`<tr class="${m.status==='no_rate'?'unused':''}">
      <td class="l">${esc(m.model)}${m.status==='no_rate'?'<span class="pill dead">no rate</span>'
        :m.status==='partial'?'<span class="pill">partial rate</span>':''}</td>
      <td>${m.turns}</td><td>${fmt(m.input)}</td><td>${fmt(m.output)}</td>
      <td>${cell(fmtCredits(m.credits))}</td><td>${cell(usd(m.usd))}</td></tr>`).join('');
  const banner = unpriced
    ? `<div class="note bad"><strong>No cost computed — per-model rates are not populated.</strong>
       Every model in this run is missing a published rate, so cost is shown as
       <em>not recorded</em> rather than $0.00. Fill in
       <span class="mono">rates.json</span> (credits per request and/or per 1M tokens, plus a
       <span class="mono">source</span> URL) and re-run <span class="mono">analyze.py</span>.</div>`
    : c.status==='partial'
      ? `<div class="note"><strong>Partial pricing — treat this as a floor.</strong>
         ${c.priced_turns} of ${c.priced_turns+c.unpriced_turns} turns priced;
         ${c.unpriced_turns} turn(s) ran on a model with no published rate, and some priced models
         bill token classes that have no rate set. The real total is higher.</div>`
      : '';
  return `<div class="card">
    <div class="hd"><h2>6 · Cost</h2>
    <p class="sub">GitHub Copilot credits billing — 1 credit = ${R.credit_usd!==null&&R.credit_usd!==undefined?usd(R.credit_usd):'?'}.
      Rates are read from <span class="mono">rates.json</span>; nothing here is inferred.</p></div>
    ${banner}
    <table><thead><tr><th class="l">Model</th><th>Turns</th><th>Input tok</th><th>Output tok</th>
      <th>Credits</th><th>Cost</th></tr></thead>
      <tbody>${rows}
      <tr class="srv"><td class="l">Total</td><td>${c.priced_turns+c.unpriced_turns}</td>
        <td>${fmt(s.summary.total_input)}</td><td>${fmt(s.summary.total_output)}</td>
        <td>${cell(fmtCredits(c.credits))}</td><td>${cell(usd(c.usd))}</td></tr></tbody></table>
    <p class="sub" style="margin-top:10px">Rate source:
      ${R.source?`<a href="${esc(R.source)}" style="color:var(--accent)">${esc(R.source)}</a>`
        :'<span class="absent">not set</span>'}
      ${R.retrieved?` · retrieved ${esc(R.retrieved)}`:''}</p>
  </div>`;
}

const CTXCOL = {
  'System prompt':'#58a6ff', 'built-in tools':'#d29922',
  'Instruction files / workspace context':'#3fb950',
  'Skill descriptions':'#6e7681', 'Conversation history':'#a371f7',
  'File contents via tool results':'#f778ba',
  'Unattributed (framing / tokenizer drift)':'#3d444d'
};
const MCPCOL = ['#f85149','#db6d28','#e3777f','#bc8cff','#39c5cf','#ff9bce'];
function ctxColor(k, i){
  if (CTXCOL[k]) return CTXCOL[k];
  if (k.startsWith('mcp: ')) return MCPCOL[i % MCPCOL.length];
  return '#484f58';
}

/* ---------------- view 4: summary strip ---------------- */
function strip(s){
  const u = s.summary;
  const t = [
    ['Total input',  fmt(u.total_input), u.total_cache_read!==null ? kt(u.total_cache_read)+' from cache' : ''],
    ['Total output', fmt(u.total_output), u.total_reasoning ? kt(u.total_reasoning)+' reasoning' : ''],
    ['Cache hit ratio', pct(u.cache_hit_ratio), 'cache_read ÷ input'],
    ['Cache creation', u.total_cache_creation===null ? null : fmt(u.total_cache_creation),
      u.total_cache_creation===null ? 'not emitted by these models' : 'on '+u.cache_creation_coverage+' turns'],
    ['Requests', u.request_count, u.request_count>1 ? 'invoke_agent roots' : ''],
    ['Turns', u.turn_count, u.reported_turn_count!==null ? 'reported: '+u.reported_turn_count : ''],
    ['Wall clock', dur(u.duration_ms), ''],
    ['Tool calls', u.tool_calls, u.tools_invoked+' distinct'],
    ['Tools offered', u.tools_offered, (u.tools_offered-u.tools_invoked)+' never called'],
    ['Errors', u.error_count, u.error_count===0 ? 'error.type absent; all Ok' : ''],
    ['Median TTFT', u.median_ttft_ms!==null ? Math.round(u.median_ttft_ms)+'ms' : null, ''],
    ['Models', u.models.length ? u.models.join(', ') : null, u.aux_chat_calls ? '+'+u.aux_chat_calls+' aux calls' : ''],
    ['Est. cost', u.cost && u.cost.usd!==null ? '$'+u.cost.usd.toFixed(2) : null,
      !u.cost ? '' : u.cost.usd!==null
        ? fmtCredits(u.cost.credits)+' credits'+(u.cost.status!=='ok'?' · incomplete':'')
        : 'rates not set'],
  ];
  return '<div class="strip">' + t.map(([k,v,n])=>
    `<div class="tile"><div class="k">${esc(k)}</div>
     <div class="v">${cell(v)}</div>${n?`<div class="n">${esc(n)}</div>`:''}</div>`).join('') + '</div>';
}

/* ---------------- view 1: token spend per turn ---------------- */
function turnChart(s){
  const T = s.turns;
  if (!T.length) return '<div class="card"><p class="sub">No agent turns in this session.</p></div>';
  const W=Math.max(560, T.length*74+90), H=320, PL=58, PR=58, PT=16, PB=46;
  const iw=W-PL-PR, ih=H-PT-PB;
  const segs=[['cache_read','Cache-read input','var(--cache)'],
              ['cache_creation','Cache-creation input','#4d8fc9'],
              ['fresh','Fresh input','var(--fresh)'],
              ['visible_output','Output','var(--out)'],
              ['reasoning','Reasoning output','var(--reason)']];
  const ccTurns=T.filter(t=>t.cache_creation!==null).length;
  const tot = t => segs.reduce((a,[k])=>a+(t[k]||0),0);
  const max = Math.max(...T.map(tot))*1.12 || 1;
  const cmax = Math.max(...T.map(t=>t.cumulative_input||0)) || 1;
  const bw = Math.min(46, iw/T.length*0.62);
  let g='';
  for(let i=0;i<=4;i++){
    const y=PT+ih-ih*i/4;
    g+=`<line x1="${PL}" y1="${y}" x2="${PL+iw}" y2="${y}" stroke="#21262d"/>
        <text x="${PL-8}" y="${y+3}" text-anchor="end">${kt(max*i/4)}</text>`;
  }
  let bars='', ann='', pts=[];
  T.forEach((t,i)=>{
    const cx=PL+iw*(i+0.5)/T.length, x=cx-bw/2;
    let acc=0, tg='';
    segs.forEach(([k,,c])=>{
      const v=t[k]||0; if(!v) return;
      const h=ih*v/max, y=PT+ih-h-acc;
      tg+=`<rect x="${x}" y="${y}" width="${bw}" height="${h}" fill="${c}">
        <title>Turn ${t.index} — ${k}: ${fmt(v)} tok — click for detail</title></rect>`;
      acc+=h;
    });
    tg+=`<rect x="${x}" y="${PT}" width="${bw}" height="${ih}" fill="transparent"/>`;
    tg+=`<text x="${cx}" y="${PT+ih+15}" text-anchor="middle">${t.index}</text>`;
    tg+=`<text x="${cx}" y="${PT+ih+28}" text-anchor="middle" style="fill:var(--dim);font-size:9px">${kt(tot(t))}</text>`;
    bars+=`<g class="bar-grp" onclick="drawerTurn(${i})">${tg}</g>`;
    // a new invoke_agent request starts here -- mark the boundary
    if(t.request_start!==null && i>0){
      const bx=cx-iw/T.length/2;
      ann+=`<g><line x1="${bx}" y1="${PT}" x2="${bx}" y2="${PT+ih+32}" stroke="var(--accent)"
        stroke-width="1" stroke-dasharray="2 3" opacity=".65"/>
        <title>New request: ${esc(t.request_start)}</title></g>`;
    }
    pts.push([cx, PT+ih-ih*(t.cumulative_input||0)/cmax]);
    if(t.fresh_jump_pct!==null){
      ann+=`<g><circle cx="${cx}" cy="${PT+ih-acc-9}" r="8" fill="rgba(248,81,73,.16)" stroke="var(--danger)"/>
        <text x="${cx}" y="${PT+ih-acc-6}" text-anchor="middle" style="fill:var(--danger);font-size:8.5px;font-weight:700">!</text>
        <title>Fresh input +${t.fresh_jump_pct}% vs previous turn</title></g>`;
    }
  });
  const line=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
  let cg='';
  for(let i=0;i<=4;i++){
    const y=PT+ih-ih*i/4;
    cg+=`<text x="${PL+iw+8}" y="${y+3}" style="fill:#7d8590">${kt(cmax*i/4)}</text>`;
  }
  const jumps=T.filter(t=>t.fresh_jump_pct!==null);
  return `<div class="card">
    <div class="hd"><h2>1 · Token spend per turn</h2>
    <p class="sub">Stacked by token class. Right axis and dashed line: cumulative input — the slope is context growth.</p></div>
    <div class="note">${ccTurns===0
      ? `Cache-creation input was <strong>not recorded</strong> for this session —
         the models in this session emit no cache-creation counter, so that segment is omitted rather than drawn as zero.`
      : `Cache-creation input <strong>is recorded</strong> on ${ccTurns}/${T.length} turns here.`}
      Cache-read and cache-creation are each a <em>subset</em> of <span class="mono">input_tokens</span>
      (verified: <span class="mono">input − cache_read − cache_creation</span> lands at 1–23 tokens and never goes
      negative), so fresh = input − cache_read − cache_creation and the stack sums to the reported total.</div>
    <div class="legend">${segs.filter(([k])=>k!=='cache_creation'||ccTurns>0)
        .map(([,l,c])=>`<span><i style="background:${c}"></i>${l}</span>`).join('')}
      ${ccTurns===0?'<span><i style="background:#6e7681"></i>Cache-creation — not recorded</span>':''}
      <span><i style="background:var(--accent)"></i>Cumulative input (right axis)</span></div>
    <div class="chartbox"><svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
      ${g}${bars}
      <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="1.6" stroke-dasharray="4 3"/>
      ${pts.map(p=>`<circle cx="${p[0]}" cy="${p[1]}" r="2.6" fill="var(--accent)"/>`).join('')}
      ${cg}${ann}
      <line x1="${PL}" y1="${PT+ih}" x2="${PL+iw}" y2="${PT+ih}" stroke="var(--bd)"/>
      <text x="${PL+iw/2}" y="${H-4}" text-anchor="middle">turn index</text>
    </svg></div>
    ${jumps.length?`<div class="note bad">Fresh-input jump &gt;25% at turn${jumps.length>1?'s':''}
      ${jumps.map(t=>`<strong>${t.index}</strong> (+${t.fresh_jump_pct}%)`).join(', ')} — cache miss or new material pulled in.</div>`
      :'<div class="note ok">No turn grew fresh input by more than 25% over the previous turn.</div>'}
  </div>`;
}

/* ---------------- view 2: context composition ---------------- */
function ctxChart(s){
  const c=s.context;
  if(!c.first) return '<div class="card"><h2>2 · Context composition</h2><p class="sub">No chat span in this session carried input messages.</p></div>';
  const rows=[['First turn (#'+c.first.turn+')',c.first]];
  if(c.last) rows.push(['Last turn (#'+c.last.turn+')',c.last]);
  else rows.push(['Last turn','__single__']);

  const keys=[]; rows.forEach(([,d])=>{ if(d!=='__single__') Object.keys(d.buckets).forEach(k=>{if(!keys.includes(k))keys.push(k)}); });
  keys.push('Unattributed (framing / tokenizer drift)');
  let mi=0; const col={}; keys.forEach(k=>{ col[k]=ctxColor(k, k.startsWith('mcp: ')?mi++:0); });

  const maxTot=Math.max(...rows.filter(r=>r[1]!=='__single__').map(([,d])=>d.reported_input||0));
  const W=1180, RH=54, PL=132, PR=96, H=rows.length*RH+34;
  let svg='', resids=[];
  rows.forEach(([lab,d],ri)=>{
    const y=ri*RH+10;
    svg+=`<text class="lbl" x="0" y="${y+21}">${esc(lab)}</text>`;
    if(d==='__single__'){ svg+=`<text x="${PL}" y="${y+21}" style="fill:var(--dim);font-style:italic">only one turn in this session</text>`; return; }
    const iw=W-PL-PR, tot=d.reported_input||1;
    const known=Object.values(d.buckets).reduce((a,v)=>a+(v||0),0);
    const resid=Math.max(0, tot-known);
    resids.push(resid/tot);
    let x=PL;
    keys.forEach(k=>{
      const v = k.startsWith('Unattributed') ? resid : d.buckets[k];
      if(!v) return;
      const w=iw*v/tot;
      const ci=CTXDATA.length;
      CTXDATA.push({rowIdx:ri, label:lab, key:k, value:v, total:tot});
      svg+=`<rect x="${x}" y="${y}" width="${w}" height="30" fill="${col[k]}" class="clickable"
        onclick="drawerCtx(${ci})">
        <title>${esc(k)}: ${fmt(v)} tok (${(v/tot*100).toFixed(1)}%) — click for detail</title></rect>`;
      if(w>44) svg+=`<text x="${x+w/2}" y="${y+19}" text-anchor="middle" style="fill:#0d1117;font-weight:700;font-size:9.5px">${(v/tot*100).toFixed(0)}%</text>`;
      x+=w;
    });
    svg+=`<text class="big" x="${W-PR+8}" y="${y+20}">${fmt(tot)}</text>`;
    svg+=`<text x="${PL}" y="${y+44}" style="fill:var(--dim);font-size:10px">reported input_tokens; unattributed residual ${(resid/tot*100).toFixed(1)}%</text>`;
  });
  const residMax = resids.length ? Math.max(...resids) : 0;
  const residTxt = resids.length===0 ? 'n/a'
    : resids.length===1 || Math.abs(resids[0]-resids[resids.length-1])<0.005
      ? (residMax*100).toFixed(1)+'%'
      : (Math.min(...resids)*100).toFixed(1)+'–'+(residMax*100).toFixed(1)+'%';
  return `<div class="card">
    <div class="hd"><h2>2 · Context composition — first turn vs last turn</h2>
    <p class="sub">Approximating <span class="mono">/context</span>. Bars are scaled to the model-reported
      <span class="mono">input_tokens</span>, so the residual is visible rather than hidden.</p></div>
    <div class="note">Token counts are <strong>approximate</strong> — ${esc(D.tokenizer.kind)} is a proxy for
      models that do not publish their tokenizer. The residual band is the gap between the sum of buckets and the
      model's own <span class="mono">input_tokens</span> (chat framing, role delimiters, tokenizer drift):
      <strong>${residTxt}</strong> in this session.
      ${residMax>0.15?`<br><strong>Treat this session's bucket sizes as a lower bound.</strong> A residual this large
        means the proxy tokenizer is a poor fit for <span class="mono">${esc(s.summary.models.join(', '))}</span> —
        every bucket is undercounted by roughly the same factor, so proportions stay informative but absolute
        numbers do not. For comparison the grok-4.5 sessions here run 3–4%.`:''}
      <strong>Skill descriptions still cannot be attributed</strong>: the skill catalogue is not separable from the
      system prompt, and the harness reports a skill name only on tool-invocation spans${HMETA.partial_attributes&&skillCoverage()
        ?` (${skillCoverage()} of ${HMETA.span_count} spans)`:''} —
      so that bucket is omitted rather than estimated.</div>
    <div class="legend">${keys.map(k=>`<span><i style="background:${col[k]}"></i>${esc(k)}</span>`).join('')}</div>
    <div class="chartbox"><svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${svg}</svg></div>
  </div>`;
}

/* ---------------- view 3: tool cost ranking ---------------- */
function toolView(s){
  const rows=s.tools.filter(t=>t.in_definitions);
  if(!rows.length) return '<div class="card"><h2>3 · Tool cost ranking</h2><p class="sub">No chat span in this session carried tool definitions.</p></div>';
  const u=s.summary;
  const max=Math.max(...rows.map(t=>t.total_schema_cost||0))||1;
  const W=1180, PL=310, PR=118, RH=17, H=rows.length*RH+12;
  const svg=rows.map((t,i)=>{
    const y=i*RH, w=(W-PL-PR)*(t.total_schema_cost||0)/max;
    const dead=t.invocations===0;
    const c=dead?'var(--danger)':(t.is_mcp?'#bc8cff':'var(--ok)');
    return `<g class="bar-grp" onclick="drawerTool(${i})"><rect x="0" y="${y}" width="${W}" height="${RH}" fill="transparent"/>
      <text x="${PL-8}" y="${y+12}" text-anchor="end" style="fill:${dead?'#ff9bce':'var(--fg)'};font-size:10px">${esc(t.name)}</text>
      <rect x="${PL}" y="${y+3}" width="${Math.max(w,1)}" height="${RH-7}" fill="${c}" opacity="${dead?.85:.72}">
        <title>${esc(t.name)}\n${fmt(t.schema_tokens)} tok × ${t.turns_resident} turns = ${fmt(t.total_schema_cost)}\ninvocations: ${t.invocations}</title></rect>
      <text x="${PL+Math.max(w,1)+7}" y="${y+12}" style="fill:${dead?'var(--danger)':'var(--mut)'};font-size:9.5px">${fmt(t.total_schema_cost)}${dead?'  never called':'  ×'+t.invocations}</text></g>`;
  }).join('');

  const byServer={};
  rows.forEach(t=>{ (byServer[t.server]=byServer[t.server]||[]).push(t); });
  const order=s.servers.filter(g=>byServer[g.server]);
  let tbl='';
  order.forEach(g=>{
    tbl+=`<tr class="srv"><td class="l">${esc(g.server)}${g.is_mcp?'<span class="pill mcp">MCP</span>':''}</td>
      <td>${fmt(g.schema_tokens)}</td><td>${g.invocations}</td><td>—</td>
      <td>${fmt(g.total_schema_cost)}</td><td>—</td><td>—</td>
      <td style="color:${g.unused_tools?'var(--danger)':'var(--mut)'}">${g.unused_tools}/${g.tools} unused · ${fmt(g.unused_cost)}</td></tr>`;
    byServer[g.server].forEach(t=>{
      const dead=t.invocations===0;
      tbl+=`<tr class="${dead?'unused':''}"><td class="l">${esc(t.name)}${dead?'<span class="pill dead">0 calls</span>':''}</td>
        <td>${fmt(t.schema_tokens)}</td><td>${t.invocations}</td><td>${t.turns_resident}</td>
        <td>${fmt(t.total_schema_cost)}</td><td>${fmt(t.cost_per_invocation)}</td>
        <td>${cell(t.result_tokens===null?null:fmt(t.result_tokens))}</td>
        <td style="color:var(--dim)">${t.results_not_recorded?t.results_not_recorded+' result(s) not recorded':''}</td></tr>`;
    });
  });

  const waste=u.unused_schema_per_turn||0, per=u.schema_tokens_per_turn||1;
  const avgIn=u.total_input/Math.max(u.turn_count,1);
  return `<div class="card">
    <div class="hd"><h2>3 · Tool cost ranking</h2>
    <p class="sub">Sorted by total schema cost (schema tokens × turns resident). Red = never invoked: overhead paid on every turn.</p></div>
    <div class="note bad"><strong>${u.tools_offered-u.tools_invoked} of ${u.tools_offered} tools were never called.</strong>
      That is <strong>${fmt(waste)} tokens per turn</strong> (${(waste/per*100).toFixed(1)}% of the
      ${fmt(u.schema_tokens_per_turn)}-token schema block), resident across ${u.defs_turns} turn(s)
      — <strong>${fmt(waste*u.defs_turns)} tokens</strong> spent on tools the model never used.
      Tool definitions are ${(per/avgIn*100).toFixed(1)}% of the average ${fmt(Math.round(avgIn))}-token context.
      ${u.schema_waste_cost && u.schema_waste_cost.usd_low!==null
        ? `<br>At published rates that is <strong>${usd(u.schema_waste_cost.usd_low)}–${usd(u.schema_waste_cost.usd_high)}</strong>
           (${fmtCredits(u.schema_waste_cost.credits_low)}–${fmtCredits(u.schema_waste_cost.credits_high)} credits) —
           a range, not a point, because only the first turn pays fresh-input rates and the rest is cached.
           Cheap in dollars precisely <em>because</em> caching absorbs it; the context-window cost is the real one.`
        : ''}</div>
    <div class="legend">
      <span><i style="background:var(--ok)"></i>built-in, invoked</span>
      <span><i style="background:#bc8cff"></i>MCP, invoked</span>
      <span><i style="background:var(--danger)"></i>never invoked</span></div>
    <div class="scroll cap"><div class="chartbox">
      <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${svg}</svg></div></div>
    <h2 style="margin:22px 0 8px">Per-tool detail, grouped by server</h2>
    <div class="scroll cap"><table>
      <thead><tr><th class="l">Tool</th><th>Schema tok</th><th>Invocations</th><th>Turns resident</th>
        <th>Total schema cost</th><th>Cost / invocation</th><th>Result tok</th><th class="l">Notes</th></tr></thead>
      <tbody>${tbl}</tbody></table></div>
  </div>`;
}

/* ---------------- view 5: trace timeline ---------------- */
const OPCOL={invoke_agent:'#58a6ff',chat:'#d29922',execute_tool:'#3fb950',
             execute_hook:'#f778ba',embeddings:'#a371f7'};
let TLATTR=[];   // span attributes, rendered lazily on click
function timeline(s){
  const flat=[];
  (function walk(ns,d){ns.forEach(n=>{flat.push([n,d]);walk(n.children||[],d+1)})})(s.timeline,0);
  if(!flat.length) return '';
  TLATTR=flat.map(([n])=>n.attributes);
  const span=Math.max(...flat.map(([n])=>n.offsetMs+(n.durationMs||0)))||1;
  const rows=flat.map(([n,d],i)=>{
    const left=(n.offsetMs/span*100), w=Math.max((n.durationMs||0)/span*100,0.4);
    const c=OPCOL[n.op]||'#6e7681';
    const tok=[n.input!==null&&n.input!==undefined?'in '+fmt(n.input):null,
               n.output!==null&&n.output!==undefined?'out '+fmt(n.output):null].filter(Boolean).join(' · ');
    return `<div>
      <div class="tlrow" onclick="tog(${i})">
        <span class="tlname" style="width:${230-d*11}px;padding-left:${d*11}px"
          title="${esc(n.name)}">${esc(n.name)}</span>
        <span style="flex:1;position:relative;height:11px;background:#0e1319;border-radius:2px">
          <span class="tlbar" style="position:absolute;left:${left}%;width:${w}%;background:${c}"></span></span>
        <span class="tlmeta" style="width:66px;text-align:right">${dur(n.durationMs)||'—'}</span>
        <span class="tlmeta" style="width:170px">${tok||''}</span>
      </div>
      <pre id="sp${i}" style="display:none" data-filled="0"></pre></div>`;
  }).join('');
  return `<div class="card">
    <div class="hd"><h2>5 · Trace timeline</h2>
    <p class="sub">Span tree, bars positioned by offset from session start. Click any span for its full attributes.</p></div>
    <div class="legend">${Object.entries(OPCOL).map(([k,v])=>
      `<span><i style="background:${v}"></i>${k}${k==='execute_hook'?' — none in this export':''}</span>`).join('')}</div>
    <div class="tl scroll cap">${rows}</div></div>`;
}
// Attribute JSON is filled in on first open. Rendering all of it up front put
// ~195 multi-KB <pre> blocks in the DOM on the combined view, which is by far
// the heaviest thing on the page.
function tog(i){
  const e=document.getElementById('sp'+i);
  if(e.dataset.filled==='0'){
    e.textContent=JSON.stringify(TLATTR[i]||{},null,2);
    e.dataset.filled='1';
  }
  e.style.display=e.style.display==='none'?'block':'none';
}

/* ---------------- assembly ---------------- */
function render(s){
  CUR_SESSION = s;
  CTXDATA = [];
  buildSpanMap(s.timeline||[]);
  closeDrawer();
  const rows=s.reconciliation||[];
  const bad=rows.filter(r=>!(r.input_match&&r.output_match));
  let rec='';
  if(rows.length){
    rec = bad.length===0
      ? `<div class="note ok"><strong>Reconciliation OK</strong> across ${rows.length}
         request${rows.length>1?'s':''} — per-turn sums equal each <span class="mono">invoke_agent</span>
         total exactly (${rows.map(r=>fmt(r.root_input)).join(' + ')} in).</div>`
      : `<div class="note bad"><strong>Reconciliation MISMATCH</strong> on ${bad.length} of ${rows.length}
         request(s):${bad.map(r=>` <em>${esc(r.request)}</em> — chat sum ${fmt(r.sum_chat_input)}
         vs root ${fmt(r.root_input)} (${(r.sum_chat_input-r.root_input>0?'+':'')}${fmt(r.sum_chat_input-r.root_input)})`).join(';')}</div>`;
  }
  const sub = s.is_subagent
    ? `<div class="note">This is a <strong>subagent session</strong> — agent
       <span class="mono">${esc(s.agent_name||'?')}</span>, spawned by a
       <span class="mono">runSubagent</span> tool call in session
       <span class="mono">${esc((s.parent_session||'?').slice(0,8))}</span>. Its tokens are counted here and
       are <em>not</em> folded into the parent's totals, so the combined view sums each span exactly once.</div>` : '';
  const reqs = s.requests && s.requests.length>1
    ? `<div class="note">${s.requests.length} user requests in this session:
       ${s.requests.map((r,i)=>`<strong>${i+1}.</strong> ${esc(r.request.slice(0,60))} <em>(${r.turns} turns)</em>`).join(' · ')}</div>` : '';
  const u=s.summary;
  const aux = u.aux_chat_calls ? `<div class="note">${u.aux_chat_calls} auxiliary
    <span class="mono">${esc(u.aux_models.join(', '))}</span> call(s) in this trace (conversation-title generation)
    are excluded from the per-turn charts but did cost ${fmt(u.aux_input)} in / ${fmt(u.aux_output)} out.</div>` : '';
  document.getElementById('meta').innerHTML =
    `${esc(s.label)}` + (s.repo?` · <span class="mono">${esc(s.branch||'')}</span> · ${esc(s.repo.split('/').pop())}`:'') +
    (s.agent_name?` · agent: ${esc(s.agent_name)}`:'') + ` · ${s.span_count} spans`;
  document.getElementById('app').innerHTML =
    strip(s)+rec+sub+reqs+aux+costView(s)+toolView(s)+turnChart(s)+ctxChart(s)+timeline(s);
}


/* ---------------- error surfacing ---------------- */
// A silent throw used to leave #app empty, which is indistinguishable from
// "the page never loaded". Always show what broke.
function fail(where, e){
  document.getElementById('app').innerHTML =
    `<div class="card"><h2 style="color:var(--danger)">Error in ${esc(where)}</h2>
     <p class="sub">Full detail:</p>
     <pre style="display:block">${esc((e&&e.stack)||String(e))}</pre></div>`;
}

async function api(path, opts){
  const r = await fetch(path, opts);
  const body = await r.json().catch(()=>null);
  if(!r.ok) throw new Error((body&&body.error) || `${path} -> HTTP ${r.status}`);
  return body;
}

/* ---------------- footer ---------------- */
function renderFoot(){
  const h = HMETA || {};
  const o = h.orphans || {};
  const part = Object.entries(h.partial_attributes || {});
  const srcs = (D.sources||[]).map(s=>`<span class="mono">${esc(s.origin)}</span> (${s.new} new)`);
  document.getElementById('foot').innerHTML =
    `Store: <span class="mono">sessions.db</span> — ${fmt(D.span_count)} spans` +
    (D.quarantined ? `, <span class="mono">${fmt(D.quarantined)}</span> quarantined (no adapter matched)` : '') +
    `. Ingested from ${srcs.join(' + ') || '—'}. Tokenizer:
     <span class="mono">${esc(D.tokenizer.kind||'?')}</span> (proxy — not billing-exact).<br>` +
    (h.missing_attributes ? `Absent from every span, shown as “not recorded” rather than substituted:
       <span class="mono">${(h.missing_attributes.map(esc).join('</span>, <span class="mono">'))||'none'}</span>.
       Span types absent: <span class="mono">${(h.absent_span_types||[]).map(esc).join(', ')||'none'}</span>.
       ${part.length?`Present on only some spans: ${part.map(([k,v])=>`<span class="mono">${esc(k)}</span> (${v}/${h.span_count})`).join(', ')}.`:''}<br>` : '') +
    (o.span_count ? `No <span class="mono">invoke_agent</span> ancestor: ${o.span_count} harness-internal spans
       (${Object.entries(o.by_agent||{}).sort((a,b)=>b[1]-a[1]).map(([k,v])=>v+'× '+k).join(', ')}) costing
       ${fmt(o.total_input)} in / ${fmt(o.total_output)} out — real spend, excluded from every session above.` : '');
}

/* ---------------- session switching ---------------- */
async function show(id){
  try{
    const p = await api('api/session/'+encodeURIComponent(id));
    CUR = id;
    HMETA = (D.harnesses||{})[p.harness] || {};
    render(p);
    renderFoot();
  }catch(e){ fail('session '+id, e); }
}

function fillPicker(){
  const pick = document.getElementById('pick');
  // aggregate last; real sessions in chronological order
  const real = SESSIONS.filter(s=>s.session_id!=='__all__');
  const agg  = SESSIONS.find(s=>s.session_id==='__all__');
  const opts = real.map(s=>[s.session_id,
      `${s.is_subagent?'↳ ':''}${(s.label||s.session_id).slice(0,52)} — ${s.turn_count} turns`
      + (s.is_subagent?` [subagent: ${s.agent_name}]`:'')]);
  if(agg) opts.push([agg.session_id, `${agg.label} — ${agg.turn_count} turns`]);
  pick.innerHTML = opts.map(([v,l])=>`<option value="${esc(v)}">${esc(l)}</option>`).join('');
  // default to the busiest real session, as before
  const busiest = real.reduce((b,s)=>(!b||s.turn_count>b.turn_count)?s:b, null);
  pick.value = CUR || (busiest ? busiest.session_id : (agg?agg.session_id:''));
  pick.onchange = ()=>show(pick.value);
  return pick.value;
}

/* ---------------- live status ----------------
   Polls a LOCAL ~200-byte endpoint. It never re-exports to check for changes;
   the follow-stream does the ingesting and this only watches a counter. */
let lastGen = null, lastSpans = null;
async function pollStatus(){
  const dot = document.getElementById('dot'), txt = document.getElementById('statustext');
  try{
    const st = await api('api/status');
    const f = st.follow || {};
    dot.className = 'dot ' + (f.alive ? 'live' : (f.error ? 'down' : 'stale'));
    txt.textContent = `${fmt(st.spans)} spans · ${st.sessions} sessions`
      + (f.alive ? ' · live' : (f.error ? ' · follow down' : ' · follow idle'))
      + (st.rebuilding ? ' · rebuilding…' : '');
    if(lastGen !== null && (st.generation !== lastGen || st.spans !== lastSpans)){
      lastGen = st.generation; lastSpans = st.spans;
      SESSIONS = (await api('api/sessions')).sessions;
      const keep = CUR;
      fillPicker();
      await show(document.getElementById('pick').value = (keep || document.getElementById('pick').value));
      return;
    }
    lastGen = st.generation; lastSpans = st.spans;
  }catch(e){
    dot.className = 'dot down';
    txt.textContent = 'serve.py unreachable';
  }
}

/* ---------------- "Pull latest" ---------------- */
document.getElementById('reload').onclick = async function(){
  const btn = this, msg = document.getElementById('reloadmsg');
  const say = (cls, html)=>{ msg.innerHTML = `<div class="note ${cls}">${html}</div>`; };
  btn.disabled = true; const label = btn.textContent; btn.textContent = '↻ Pulling…';
  say('', 'Exporting spans from the Aspire dashboard and reconciling…');
  try{
    const j = await api('api/refresh', {method:'POST'});
    if(!j.new_spans){
      say('ok', `<strong>Already up to date.</strong> Exported ${fmt(j.exported_spans)} spans — none were new.`
        + (j.quarantined?` ${fmt(j.quarantined)} quarantined (no adapter matched).`:''));
    }else{
      say('ok', `<strong>${fmt(j.new_spans)} new span(s)</strong> from <span class="mono">${esc(j.file)}</span> —
        now ${fmt(j.total_spans)} in the store.`);
      SESSIONS = (await api('api/sessions')).sessions;
      fillPicker();
      await show(document.getElementById('pick').value);
    }
  }catch(e){
    say('bad', `<strong>Refresh failed.</strong><pre style="display:block">${esc(String(e))}</pre>`);
  }finally{
    btn.disabled = false; btn.textContent = label;
  }
};

/* ---------------- boot ---------------- */
(async function(){
  try{
    const [meta, list] = await Promise.all([api('api/meta'), api('api/sessions')]);
    D = Object.assign(D, meta);
    SESSIONS = list.sessions;
    if(!SESSIONS.length){
      document.getElementById('app').innerHTML =
        `<div class="card"><h2>No sessions yet</h2>
         <p class="sub">The store has ${fmt(meta.span_count)} spans`
         + (meta.quarantined?` and ${fmt(meta.quarantined)} quarantined spans (no adapter matched)`:'')
         + `. Click <strong>↻ Pull latest</strong>, or ingest an export:
         <span class="mono">python3 -m agentdash.ingest spans*.json</span></p></div>`;
      renderFoot();
    }else{
      await show(fillPicker());
    }
    pollStatus();
    setInterval(pollStatus, 4000);
  }catch(e){ fail('startup', e); }
})();
