import { useEffect, useState } from "react";
import { api, when } from "../api";
import { Table } from "../components";

export function LearningPanel({ accountId, datasets, learning, act }: any) {
  const [dataset, setDataset] = useState("");
  const [securities, setSecurities] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<any>(null);
  useEffect(() => {
    let disposed = false;
    setSecurities([]);
    setError("");
    if (dataset)
      api(`/learning/securities?dataset_id=${dataset}`)
        .then((r) => {
          if (!disposed) setSecurities(r);
        })
        .catch((e) => {
          if (!disposed) setError(String(e));
        });
    return () => {
      disposed = true;
    };
  }, [dataset]);
  return (
    <section aria-label="CSV strategy learning">
      <h3>Learn from daily CSV history</h3>
      <p>
        Import your combined NSE CSV in Data Management, then study a security
        here. Learning uses the selected account's capital, risk and costs.
        Configure positive slippage and fees when creating that account. At
        least 600 daily bars are required per security.
      </p>
      <p>
        Settings are selected on earlier data and checked on a separate later
        period, including doubled-cost testing. Live use follows the current
        daily trend and requires passing evidence for that trend. No qualifying
        strategy means no entry. This does not guarantee profit.
      </p>
      {error && <p className="error">{error}</p>}
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          act(async () => {
            await api("/learning", "POST", {
              account_id: Number(accountId),
              dataset_id: Number(dataset),
              instrument_id: Number(f.get("security")),
              max_quantity: Number(f.get("units")),
              train_until: f.get("until") || null,
            });
          });
        }}
      >
        <label>
          Learning dataset
          <select
            value={dataset}
            required
            onChange={(e) => setDataset(e.target.value)}
          >
            <option value="">Select NSE daily dataset</option>
            {datasets
              .filter((d: any) => d.timeframe === "1d")
              .map((d: any) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          Learning security
          <select name="security" required key={dataset}>
            <option value="">Select security</option>
            {securities.map((s: any) => (
              <option key={s.instrument_id} value={s.instrument_id}>
                {s.ticker} · {s.isin} · {s.bars} days
              </option>
            ))}
          </select>
        </label>
        <label>
          Training maximum units
          <input
            name="units"
            type="number"
            min="1"
            max="1000000"
            defaultValue="1"
            required
          />
        </label>
        <label>
          Study through (optional)
          <input name="until" type="date" />
        </label>
        <button className="primary" disabled={!accountId || !dataset}>
          Study and validate strategies
        </button>
      </form>
      <p>
        Leave the date blank to study all available history. For forward replay,
        choose an earlier cutoff: the resulting profile can only trade after its
        holdout ends. Daily profiles expire after 90 calendar days; retrain
        using updated history. Repeatedly choosing profiles by their holdout
        profits can overfit.
      </p>
      <Table
        rows={learning.jobs}
        columns={[
          { key: "id", label: "Study" },
          { key: "status", label: "Status" },
          { key: "progress", label: "Progress %" },
          { key: "error", label: "Reason" },
        ]}
      />
      <Table
        rows={learning.profiles}
        columns={[
          { key: "id", label: "Profile" },
          { key: "ticker", label: "Security" },
          {
            key: "approved",
            label: "Validation",
            render: (r: any) =>
              r.approved
                ? "Passing settings available"
                : "No qualifying settings",
          },
          {
            key: "trained_through",
            label: "Evidence through",
            render: (r: any) => when(r.trained_through),
          },
          {
            key: "review",
            label: "Evidence",
            render: (r: any) => (
              <button
                type="button"
                onClick={() =>
                  act(async () =>
                    setDetail(await api(`/learning/profiles/${r.id}`)),
                  )
                }
              >
                Review profile {r.id}
              </button>
            ),
          },
        ]}
      />
      {detail && (
        <details open>
          <summary>
            Profile {detail.id}: training, validation and holdout evidence
          </summary>
          <p>{detail.evidence.method}</p>
          <p>{detail.evidence.limitations}</p>
          <Table
            rows={detail.evidence.winners}
            columns={[
              { key: "regime", label: "Trend" },
              { key: "strategy", label: "Strategy" },
              { key: "atr_stop", label: "ATR stop" },
              {
                key: "approved",
                label: "Passed",
                render: (r: any) => (r.approved ? "Yes" : "No"),
              },
              { key: "reason", label: "Reason" },
            ]}
          />
          <details>
            <summary>All candidate metrics and assumptions</summary>
            <pre>{JSON.stringify(detail.evidence, null, 2)}</pre>
          </details>
        </details>
      )}
    </section>
  );
}
