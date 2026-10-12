import { useEffect, useState } from 'react';
import { api, rupees } from '../api';
export function AutoPaperPage({datasets,refresh}:any) {
  const [data,setData]=useState<any>({sessions:[],stocks:[],stream:{}});
  const [account,setAccount]=useState('');
  const [dataset,setDataset]=useState('');
  const [capital,setCapital]=useState('5000');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [query,setQuery]=useState('');
  const [message,setMessage]=useState('');
  const load=async()=>setData(await api('/auto-paper'+(account?'?account_id='+account:'')));
  useEffect(()=>{let active=true; const poll=async()=>{try {const d=await api('/auto-paper'+(account?'?account_id='+account:''));if(active)setData(d);}catch(e:any){if(active)setError(e.message);}};poll();const t=setInterval(poll,3000);return()=>{active=false;clearInterval(t);};},[account]);
  const act=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn();await load();}catch(e:any){setError(e.message);}finally{setBusy(false);}};
  const selected=account||String(data.sessions[0]?.account_id||'');
  const session=data.sessions.find((s:any)=>String(s.account_id)===selected);
  const snap=data.stream.snapshots?.find((s:any)=>s.id===Number(selected));
  const stocks=data.stocks.filter((s:any)=>(s.ticker+' '+s.status).toLowerCase().includes(query.toLowerCase()));
  const counts=data.stocks.reduce((acc:any,s:any)=>({...acc,[s.status]:(acc[s.status]||0)+1}),{});
  return <div className="activity-page">
    <h2>Automatic paper trading</h2>
    <p>Upload your NSE CSV, enter virtual capital, then start. The app matches stock keys, chooses strategies and sizes simulated orders for you.</p>
    {error&&<p className="error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    <section><h3>1. Choose your stock list</h3>
      <p>A one-day or three-year NSE bhavcopy CSV works. Its EQ-series ISINs are checked against today's Upstox instrument master. Historical CSV prices are not used as live fills.</p>
      <form onSubmit={e=>{e.preventDefault();const form=e.currentTarget;act(async()=>{const f=new FormData(form);f.set('source','User-uploaded NSE bhavcopy for personal paper research');const r=await api('/import/nse','POST',f);await refresh();setDataset(String(r.id));setMessage('CSV imported. Enter capital and prepare your account.');form.reset();});}}>
        <label>NSE CSV<input name="file" type="file" accept=".csv" required/></label><button disabled={busy}>Upload CSV</button>
      </form>
      <label>Or use an imported dataset<select value={dataset} onChange={e=>setDataset(e.target.value)}><option value="">Choose NSE dataset</option>{datasets.filter((d:any)=>d.timeframe==='1d').map((d:any)=><option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
    </section>
    <section><h3>2. Set virtual capital</h3>
      <label>Capital (INR)<input type="number" min="1" max="100000000" value={capital} onChange={e=>setCapital(e.target.value)}/></label>
      <p>Automatic sizing: 1% risk per trade, 2% daily loss limit, one open position, no leverage. A 5% observed equity drawdown stops new entries.</p>
      <p>Simulation costs: brokerage 0.05% of each order, capped at ₹20; slippage 0.05%, plus estimated charges. These are preset assumptions, not your exact Upstox bill. The percentage charge scales with order size, not account capital.</p>
      <button className="primary" disabled={busy||!dataset||Number(capital)<1} onClick={()=>act(async()=>{const r=await api('/auto-paper/prepare','POST',{dataset_id:Number(dataset),initial_capital:Number(capital)});setAccount(String(r.account_id));await refresh();setMessage(`Prepared ${r.matched} stocks; ${r.skipped.length} unmatched. Click Start below.`);})}>{busy?'Working…':'Prepare automatic account'}</button>
    </section>
    <section><h3>3. Start and monitor</h3>
      <label>Automatic account<select value={selected} onChange={e=>setAccount(e.target.value)}><option value="">Choose account</option>{data.sessions.map((s:any)=><option key={s.account_id} value={s.account_id}>{s.name} · {rupees(s.initial_capital)}</option>)}</select></label>
      <button className="primary" disabled={busy||!selected||session?.enabled===1} onClick={()=>act(async()=>{await api(`/auto-paper/${selected}/start`,'POST');setMessage('Started. Stocks need 200 completed live minute bars before strategy ranking.');})}>Start automatic paper trading</button>
      <button disabled={busy||!selected} onClick={()=>act(async()=>{await api(`/auto-paper/${selected}/pause`,'POST');setMessage('New entries paused. Keep the feed connected for protective exits.');})}>Pause new trades</button>
      <p><strong>{session?.enabled?'SCANNING':'PAUSED / READY'}</strong> · Feed: {data.stream.state||'STOPPED'} · Stocks: {data.stocks.length} · Equity: {rupees(snap?.equity_paise)}</p>
      {data.stream.accounts?.includes(Number(selected)) && <p role="status"><strong>Batch coverage: {data.stream.subscribed_count ?? 0} active / {data.stream.queued_count ?? 0} queued / {data.stream.universe_count ?? 0} total</strong>{data.stream.queued_count > 0 && <> · Next rotation after about {data.stream.rotation_after_market_minutes} more minutes of open-market feed updates.</>}</p>}
      <p>Large CSVs are kept intact. Up to 2,000 stocks receive the full feed at once; the rest wait for automatic rotation after 220 minutes of open-market feed updates. Held stocks and pending orders stay subscribed. Newly added stocks need their own warm-up, so checking the whole list may span more than one trading day.</p>
      {data.stream.error&&<p className="error">{data.stream.error}</p>}
      <p>First-run warm-up needs 200 completed live one-minute bars per stock (roughly 3 hours 20 minutes of continuous data). Missing bars or reconnects restart that stock's warm-up. One-day CSVs identify stocks; they cannot supply intraday strategy history. No qualifying strategy means no trade.</p>
      <p>After a closed loss: pause entries for 10 minutes, exclude that strategy for 100 minutes, and reduce the risk budget. Ranking uses rolling past simulations, not a separately validated prediction of profit. Stocks are checked sequentially; this is not a market-wide best-stock optimizer.</p>
      <p>{Object.entries(counts).map(([k,v])=>`${k.replaceAll('_',' ')}: ${v}`).join(' · ')}</p>
      {session?.state.halted&&<p className="error">Drawdown stop reached. Entries remain disabled for this account.</p>}
      {session?.state.skipped?.length>0&&<details><summary>Unmatched stocks ({session.state.skipped.length})</summary>{session.state.skipped.map((s:any,n:number)=><p key={n}>{s.symbol}: {s.reason}</p>)}</details>}
      <label>Find stock or status<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="RELIANCE or WARMING"/></label>
      <div style={{maxHeight:420,overflow:'auto'}}><table><thead><tr><th>Stock</th><th>Status</th><th>Strategy / progress</th></tr></thead><tbody>{stocks.map((s:any)=><tr key={s.instrument_id}><td>{s.ticker}</td><td>{s.status.replaceAll('_',' ')}</td><td>{s.detail.strategy||s.detail.reason|| (s.detail.bars!=null?`${s.detail.bars} / ${s.detail.required||200} bars`:'—')}</td></tr>)}</tbody></table></div>
      <h3>Recent paper orders</h3>
      {!data.orders?.length ? <p>No paper orders yet. Check stock statuses above for the reason.</p> : <div style={{overflow:'auto'}}><table><thead><tr><th>Instrument</th><th>Order</th><th>Units</th><th>Status</th><th>Reason</th></tr></thead><tbody>{data.orders.map((o:any)=><tr key={o.id}><td>{o.symbol.split(' | ').pop()}</td><td>{o.side} · {o.strategy}</td><td>{o.quantity}</td><td>{o.status}</td><td>{o.reason||'—'}</td></tr>)}</tbody></table></div>}
      <p>Orders, fills and activity are available in Activity &amp; Automation and Paper Portfolio; select this automatic account there. Upstox supplies data only. All orders remain simulated.</p>
    </section>
  </div>;
}
