import { useEffect, useState } from 'react';
import { api, rupees, when } from '../api';

export function FnoAutoPage({refresh,onAccount}:any) {
  const [data,setData]=useState<any>({sessions:[],orders:[],events:[],contracts:[],stream:{}});
  const [account,setAccount]=useState('');
  const [capital,setCapital]=useState('5000');
  const [risk,setRisk]=useState('1');
  const [product,setProduct]=useState('AUTO');
  const [adding,setAdding]=useState(false);
  const [stocks,setStocks]=useState<any[]>([]);
  const [stock,setStock]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const selected=account||String(data.selected_account||'');
  const session=data.sessions.find((s:any)=>String(s.account_id)===selected);
  const snapshot=data.snapshot;
  const load=async(id=account)=>{
    const d=await api('/fno-auto'+(id?'?account_id='+id:''));setData(d);
    if(d.selected_account)onAccount(String(d.selected_account));
  };
  useEffect(()=>{let active=true;let running=false;const poll=async()=>{
    if(running)return;running=true;
    try {const d=await api('/fno-auto'+(account?'?account_id='+account:''));if(active){setData(d);if(d.selected_account)onAccount(String(d.selected_account));}}
    catch(e:any){if(active)setError(e.message);}finally{running=false;}
  };poll();const timer=setInterval(poll,2000);return()=>{active=false;clearInterval(timer);};},[account]);
  const act=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');setNotice('');try{await fn();}catch(e:any){setError(e.message);}finally{setBusy(false);}};
  const create=async()=>{
    const r=await api('/fno-auto/setup','POST',{market:adding?'STOCK':'NIFTY',underlying_key:adding?stock:'NSE_INDEX|Nifty 50',initial_capital:Number(capital),risk_pct:Number(risk),product});
    setAccount(String(r.account_id));setAdding(false);await refresh();await load(String(r.account_id));
    setNotice(r.reused?'Your existing NIFTY account is selected. Its initial capital is unchanged.':'Account created. Press Start automated F&O to connect and begin.');
  };
  const positions=snapshot?.basket_positions||[];
  const pnlClass=(n:number|null)=>n==null?'':n>=0?'fno-positive':'fno-negative';
  return <div className="activity-page fno-page">
    <div className="fno-heading"><div><span className="tag">INTRADAY · PAPER ONLY</span><h2>Automated F&O trading</h2><p>NIFTY 50 index futures and options by default. Add stock F&O in a separate paper account.</p></div><span className="fno-feed">Feed · {data.stream.state||'STOPPED'}</span></div>
    {error&&<p className="error" role="alert">{error}</p>}
    {notice&&<p role="status">{notice}</p>}
    {(!data.sessions.length||adding)&&<section aria-label="Create automated F&O account"><h3>{adding?'Add stock F&O account':'Create your default NIFTY 50 account'}</h3>
      <p>{adding?'Choose an eligible stock. Its contracts and capital stay separate from NIFTY.':'This trades contracts on the NIFTY 50 index, not the 50 constituent stocks. No CSV or instrument keys needed.'}</p>
      <div className="fno-form">
        {adding&&<label>Stock F&O underlying<select value={stock} onChange={e=>setStock(e.target.value)}><option value="">Choose eligible stock</option>{stocks.map(s=><option key={s.key} value={s.key}>{s.label}</option>)}</select></label>}
        <label>Initial virtual capital (INR)<input type="number" min="1" max="100000000" value={capital} onChange={e=>setCapital(e.target.value)}/></label>
        <label>Contracts<select value={product} onChange={e=>setProduct(e.target.value)}><option value="AUTO">Automatic — options, then futures if eligible</option><option value="OPTIONS">Long calls / puts only</option><option value="FUTURES">Long / short futures only</option></select></label>
        <label>Maximum risk per trade (%)<input type="number" min="0.1" max="10" step="0.1" value={risk} onChange={e=>setRisk(e.target.value)}/></label>
      </div>
      <p>One full lot at a time. At ₹5,000 and 1% risk, the risk budget is only ₹50: F&O orders will usually be blocked. The app shows the reason and never adds capital or reduces the lot size.</p>
      <button className="primary" disabled={busy||Number(capital)<1||Number(risk)<=0||Number(risk)>10||(adding&&!stock)} onClick={()=>act(create)}>{busy?'Working…':adding?'Create separate stock account':'Create NIFTY account'}</button>
      {adding&&<button onClick={()=>setAdding(false)}>Cancel</button>}
    </section>}
    {data.sessions.length>0&&<section aria-label="F&O account controls">
      <div className="fno-form"><label>F&O paper account<select value={selected} onChange={e=>{setAccount(e.target.value);setData((d:any)=>({...d,snapshot:null,orders:[],events:[],contracts:[]}));setError('');}}>{data.sessions.map((s:any)=><option key={s.account_id} value={s.account_id}>{s.label}{s.market==='NIFTY'?' · DEFAULT':''} · {rupees(s.initial_capital)}</option>)}</select></label>
      <button disabled={busy} onClick={()=>act(async()=>{setStocks(await api('/fno-auto/stocks'));setAdding(true);})}>Add stock F&O account</button></div>
      <div className="fno-actions"><button className="primary" disabled={busy||!selected||!!session?.enabled} onClick={()=>act(async()=>{setNotice('Discovering contracts and loading minute history…');await api(`/fno-auto/${selected}/start`,'POST');setNotice('Started. Orders and positions will appear below automatically.');})}>{busy?'Working…':'Start automated F&O'}</button>
      <button disabled={busy||!session?.enabled} onClick={()=>act(async()=>{await api(`/fno-auto/${selected}/pause`,'POST');})}>Pause new entries</button>
      <button disabled={busy||!positions.length} onClick={()=>act(async()=>{await api(`/fno-auto/${selected}/close`,'POST');})}>Close paper position & pause</button></div>
      <div className="fno-status" role="status"><strong>{(session?.state.status||'READY').replaceAll('_',' ')}</strong><p>{session?.state.message}</p>{session?.state.detail&&<p>{session.state.detail}</p>}
      <small>{session?.enabled?'Automation enabled':'Entries paused'} · Strategy: {session?.state.strategy||'Waiting'} · Last feed message: {when(data.stream.last_message)}</small></div>
      {data.stream.error&&<p className="error">{data.stream.error}</p>}
    </section>}
    <div className="fno-metrics">
      {[['Virtual equity',snapshot?.equity_paise],['Available cash',snapshot?.available_cash_paise],['Realised P&L before costs',snapshot?.realized_pnl],['Simulation costs',snapshot?.costs]].map(([label,value])=><div key={label as string}><span>{label}</span><strong>{rupees(value as number)}</strong></div>)}
    </div>
    <section aria-label="Open F&O positions"><h3>Open positions <span className="tag">{positions.length}</span></h3><p>Updates every two seconds. Unavailable prices indicate stale or missing data.</p>
      {!positions.length?<div className="fno-empty">No open paper positions. Filled trades appear here automatically.</div>:<div className="fno-table"><table><thead><tr><th>Contract</th><th>Side / units</th><th>Entry</th><th>Live mark</th><th>Unrealised P&L</th><th>Stop / target</th><th>Status</th></tr></thead><tbody>{positions.map((p:any)=><tr key={p.id}><td><strong>{p.contract}</strong><br/>{p.kind} · {p.expiry}</td><td>{p.signed_quantity>0?'LONG':'SHORT'} · {Math.abs(p.signed_quantity)}</td><td>{rupees(p.basis_price)}</td><td>{rupees(p.mark_paise)}</td><td className={pnlClass(p.unrealized_pnl_paise)}>{rupees(p.unrealized_pnl_paise)}</td><td>{rupees(p.stop_paise)} / {rupees(p.target_paise)}</td><td>{p.status==='CLOSING'?'EXIT PENDING':p.status}</td></tr>)}</tbody></table></div>}
    </section>
    <section aria-label="F&O orders and fills"><h3>Orders & completed trades</h3>
      {!data.orders.length?<div className="fno-empty">No paper orders yet. The status above explains what the system is waiting for.</div>:<div className="fno-table"><table><thead><tr><th>Order / time (IST)</th><th>Contract / strategy</th><th>Side / units</th><th>Status</th><th>Entry / exit</th><th>Net P&L</th><th>Reason</th></tr></thead><tbody>{data.orders.map((o:any)=><tr key={o.id}><td>#{o.id}<br/>{when(o.created_at)}</td><td><strong>{o.contract}</strong><br/>{o.signal}</td><td>{o.quantity>0?'BUY':'SELL'} · {Math.abs(o.quantity)}</td><td><span className="tag">{o.status==='OPEN'?'FILLED / OPEN':o.status}</span></td><td>{o.entry_paise==null?'—':rupees(o.entry_paise)} / {o.exit_paise==null?'—':rupees(o.exit_paise)}</td><td className={pnlClass(o.net_pnl)}>{o.net_pnl==null?'—':rupees(o.net_pnl)}</td><td>{o.reason||o.exit_reason||'—'}</td></tr>)}</tbody></table></div>}
    </section>
    <section><h3>Automatic activity</h3>{!data.events.length?<p>Activity appears here when automation starts.</p>:<ul className="fno-events">{data.events.map((e:any)=><li key={e.id}><time>{when(e.timestamp)}</time><strong>{e.action.replaceAll('_',' ')}</strong><span>{Object.entries(JSON.parse(e.details)).map(([k,v])=>`${k}: ${v}`).join(' · ')}</span></li>)}</ul>}</section>
    <details><summary>How automation works, costs and limits</summary><p>The app loads recent minute history, then requires two completed live minutes (usually 2–3 minutes) after connecting. At least 60 total completed bars are required. It selects trend, breakout or range-reversal rules; a bullish signal can buy a call or future, and a bearish signal can buy a put or sell a future. Automatic mode tries near-the-money long options first, then futures. It may wait indefinitely if no signal or affordable lot qualifies.</p>
      <p>Entries: 09:15–15:00 IST on normal weekdays. Exit requests: from 15:15 IST, or on stop/target. Keep the app and feed running. A disconnected feed cannot fill an exit; the position remains visibly pending. Expiry-day entries are excluded. Stock F&O is intraday only; this screen does not arrange physical delivery.</p>
      <p>Costs are set automatically by product: ₹20 brokerage per order, 18% GST on brokerage/exchange/SEBI charges, plus estimated transaction levies and slippage. Options use 5 bps slippage; futures use 1 bp. These are simulation assumptions, not a verified Upstox invoice. Futures reserve 25% of notional as assumed collateral, not broker SPAN margin. Long-option risk uses the entire premium plus costs; stop orders do not guarantee a bounded loss.</p>
      <p>One position per account; six entries per day; 2% daily loss budget. A closed loss pauses entries for 10 minutes, excludes that signal rule for 100 minutes and halves the risk budget (minimum one-eighth). A 5% observed equity drawdown permanently stops entries. These are deterministic rules, not AI training, and do not guarantee profit. Restarting the app pauses entries; press Start to reconnect protection.</p>
      <h4>Discovered contracts</h4><div className="fno-table"><table><thead><tr><th>Contract</th><th>Lot</th><th>Expiry</th><th>Fresh price</th></tr></thead><tbody>{data.contracts.map((c:any)=><tr key={c.id}><td>{c.metadata_source.split(' | ').pop()}</td><td>{c.lot_size}</td><td>{c.expiry}</td><td>{rupees(c.price_paise)}</td></tr>)}</tbody></table></div>
    </details>
  </div>;
}
