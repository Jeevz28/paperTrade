import { useState } from "react";
import { Play } from "lucide-react";
import { api, rupees } from "../api";
import { Table, Metrics, EquityChart, Empty } from "../components";

export function ResearchPage({
  datasets,
  instruments,
  strategies,
  act,
  research = false,
}: any) {
  const [dataset, setDataset] = useState(""),
    [instrument, setInstrument] = useState(""),
    [strategy, setStrategy] = useState("breakout");
  const [capital, setCapital] = useState("1000"),
    [risk, setRisk] = useState("1"),
    [daily, setDaily] = useState("2"),
    [costs, setCosts] = useState('{"brokerage_paise":0,"slippage_bps":0}');
  const [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [atr, setAtr] = useState("1.5");
  const [result, setResult] = useState<any>(null),
    [comparison, setComparison] = useState<any[]>([]),
    [study, setStudy] = useState<any>(null);
  const [strata, setStrata] = useState<any>(null);
  const body = () => ({
    dataset_id: Number(dataset),
    instrument_id: Number(instrument),
    strategy,
    initial_capital: Number(capital),
    risk: { risk_pct: Number(risk), daily_loss_pct: Number(daily) },
    costs: JSON.parse(costs),
    parameters: { atr_stop: Number(atr) },
    start: start ? new Date(start).toISOString() : null,
    end: end ? new Date(end).toISOString() : null,
  });
  return (
    <>
      <div className="section-head">
        <div>
          <h2>{research ? "Historical research" : "Backtest workspace"}</h2>
          <p>
            Observed data only. Simulated results are not a return forecast.
          </p>
        </div>
        <span className="tag">OUT-OF-SAMPLE AWARE</span>
      </div>
      {strata && (
        <section>
          <h3>Trade strata</h3>
          <p>{strata.warning}</p>
          {Object.entries(strata.groups).map(([name, rows]) => (
            <div key={name}>
              <h4>{name}</h4>
              <Table
                rows={rows as any[]}
                columns={[
                  { key: "group", label: "Group" },
                  { key: "trades", label: "Trades" },
                  {
                    key: "net_pnl_paise",
                    label: "Net P&L",
                    render: (r: any) => rupees(r.net_pnl_paise),
                  },
                  {
                    key: "win_rate_95pct_interval",
                    label: "Win rate 95% interval",
                    render: (r: any) =>
                      r.win_rate_95pct_interval
                        ?.map((n: number) => (100 * n).toFixed(1) + "%")
                        .join(" – ") ?? "Unavailable",
                  },
                ]}
              />
            </div>
          ))}
        </section>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          act(async () => {
            const r = await api("/backtests", "POST", body());
            setResult(r);
            setStrata(await api(`/backtests/${r.id}/analytics`));
          });
        }}
      >
        <div className="toolbar form-toolbar">
          <label>
            Dataset
            <select
              required
              value={dataset}
              onChange={(e) => setDataset(e.target.value)}
            >
              <option value="">Select dataset</option>
              {datasets.map((d: any) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Instrument
            <select
              required
              value={instrument}
              onChange={(e) => setInstrument(e.target.value)}
            >
              <option value="">Select instrument</option>
              {instruments.map((i: any) => (
                <option key={i.id} value={i.id}>
                  {i.symbol}
                </option>
              ))}
            </select>
          </label>
          <label>
            Strategy
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
            >
              {strategies.map((s: any) => (
                <option key={s.name}>{s.name}</option>
              ))}
            </select>
          </label>
          <button className="primary">
            <Play size={15} /> Run backtest
          </button>
        </div>
        <details className="configuration">
          <summary>Capital, risk, costs and date range</summary>
          <div className="form-grid">
            <label>
              Starting capital (INR)
              <input
                type="number"
                min="1"
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
              />
            </label>
            <label>
              Risk per trade (%)
              <input
                type="number"
                min=".01"
                max="10"
                step=".01"
                value={risk}
                onChange={(e) => setRisk(e.target.value)}
              />
            </label>
            <label>
              Daily loss limit (%)
              <input
                type="number"
                min=".01"
                max="100"
                step=".01"
                value={daily}
                onChange={(e) => setDaily(e.target.value)}
              />
            </label>
            <label>
              ATR stop multiplier
              <input
                type="number"
                min=".1"
                max="10"
                step=".1"
                value={atr}
                onChange={(e) => setAtr(e.target.value)}
              />
            </label>
            <label>
              From (your browser timezone)
              <input
                type="datetime-local"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label>
              Through (your browser timezone)
              <input
                type="datetime-local"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
            <label className="full">
              Simulated cost configuration (JSON)
              <textarea
                value={costs}
                onChange={(e) => setCosts(e.target.value)}
              />
            </label>
          </div>
        </details>
      </form>
      <div className="notice subtle">
        Execution: next candle open. Stops and targets checked at observed opens
        only. Zero-cost defaults are uncalibrated; configure actual assumptions
        before interpreting results.
      </div>
      {research && (
        <div className="toolbar">
          <button
            disabled={!dataset || !instrument}
            onClick={() =>
              act(async () =>
                setComparison(await api("/research/compare", "POST", body())),
              )
            }
          >
            Compare strategies
          </button>
          <button
            disabled={!dataset || !instrument}
            onClick={() =>
              act(async () =>
                setStudy(await api("/research/study", "POST", body())),
              )
            }
          >
            Event study
          </button>
          <button
            disabled={!dataset || !instrument}
            onClick={() =>
              act(async () =>
                setStudy(await api("/research/walk-forward", "POST", body())),
              )
            }
          >
            Walk-forward
          </button>
        </div>
      )}
      {comparison.length > 0 && (
        <Table
          rows={comparison}
          columns={[
            { key: "strategy", label: "Strategy" },
            { key: "total_trades", label: "Closed trades" },
            { key: "win_rate_pct", label: "Win %" },
            {
              key: "net_pnl_paise",
              label: "Net P&L",
              render: (r) => rupees(r.net_pnl_paise),
            },
            { key: "max_drawdown_pct", label: "Drawdown %" },
            { key: "profit_factor", label: "Profit factor" },
          ]}
        />
      )}
      {study && (
        <section className="results">
          <h3>{study.events ? "Event study" : "Walk-forward results"}</h3>
          {study.events ? (
            <>
              <Table
                rows={study.events}
                columns={[
                  { key: "event", label: "Event" },
                  { key: "observations", label: "Observations" },
                  { key: "positive_outcome_pct", label: "Positive %" },
                  {
                    key: "mean_forward_return_pct",
                    label: "Mean forward return %",
                  },
                ]}
              />
              <p>{study.warnings.join(" ")}</p>
              <h4>Regime distribution</h4>
              <Table
                rows={Object.entries(study.regime_distribution).map(
                  ([regime, bars]) => ({ regime, bars }),
                )}
                columns={[
                  { key: "regime", label: "Regime" },
                  { key: "bars", label: "Bars" },
                ]}
              />
            </>
          ) : (
            <>
              <p>{study.method}</p>
              <Table
                rows={study.windows}
                columns={[
                  { key: "test_start", label: "Test start" },
                  { key: "test_end", label: "Test end" },
                  { key: "selected_parameters", label: "Selected parameters" },
                  { key: "out_of_sample", label: "OOS metrics" },
                ]}
              />
            </>
          )}
        </section>
      )}
      {result ? (
        <>
          <Metrics metrics={result.metrics} />
          <EquityChart rows={result.equity_curve} />
          <div className="notice">{result.warnings.join(" ")}</div>
          <Table
            rows={result.trades}
            columns={[
              { key: "entry_time", label: "Entry" },
              { key: "exit_time", label: "Exit" },
              { key: "quantity", label: "Qty" },
              {
                key: "net_pnl",
                label: "Net P&L",
                render: (r) => rupees(r.net_pnl),
              },
              { key: "regime", label: "Regime" },
            ]}
            empty="No closed trades for these rules and this dataset"
          />
          <details>
            <summary>
              Rejected entries ({result.rejected.length}) and full metrics
            </summary>
            <pre>
              {JSON.stringify(
                {
                  metrics: result.metrics,
                  rejected: result.rejected,
                  open_position: result.open_position,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </>
      ) : (
        <Empty title="No backtest has been run">
          Select an imported dataset, instrument and strategy.
        </Empty>
      )}
    </>
  );
}
