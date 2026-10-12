import { useState } from "react";
import { Upload, Plus, Trash2 } from "lucide-react";
import { api, when } from "../api";
import { Modal, Table } from "../components";

export function DataPage({ instruments, datasets, refresh, act }: any) {
  const [modal, setModal] = useState(false),
    [file, setFile] = useState<File | null>(null),
    [source, setSource] = useState(""),
    [tf, setTf] = useState("5m");
  const [kind, setKind] = useState("SPOT");
  const [format, setFormat] = useState("standard");
  const [report, setReport] = useState<any>(null);
  const [quality, setQuality] = useState<any[]>([]);
  return (
    <>
      <div className="section-head">
        <div>
          <h2>Data workspace</h2>
          <p>Source provenance, contract specifications and import quality.</p>
        </div>
        <button onClick={() => setModal(true)}>
          <Plus size={16} /> Register instrument
        </button>
      </div>
      <form
        className="import-band"
        onSubmit={(e) => {
          e.preventDefault();
          act(async () => {
            if (!file) throw Error("Choose a file");
            const form = new FormData();
            form.append("file", file);
            form.append("source", source);
            form.append("timeframe", tf);
            setReport(
              await api(
                format === "nse" ? "/import/nse" : "/import",
                "POST",
                form,
              ),
            );
            await refresh();
          });
        }}
      >
        <label>
          File format
          <select value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="standard">Standard OHLC CSV / Parquet</option>
            <option value="nse">Combined NSE bhavcopy CSV (daily)</option>
          </select>
        </label>
        <label>
          Dataset
          <input
            type="file"
            accept={format === "nse" ? ".csv" : ".csv,.parquet"}
            required
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <label>
          Data source / permission
          <input
            value={source}
            required
            placeholder="Provider or export provenance"
            onChange={(e) => setSource(e.target.value)}
          />
        </label>
        <label>
          Timeframe
          <select
            disabled={format === "nse"}
            value={format === "nse" ? "1d" : tf}
            onChange={(e) => setTf(e.target.value)}
          >
            {["1m", "3m", "5m", "10m", "15m", "30m", "1h", "1d"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
        <button className="primary">
          <Upload size={16} /> Validate & import
        </button>
      </form>
      {format === "nse" && (
        <p className="notice">
          Upload your single nse_bhavcopy_combined.csv with its original
          headers. Up to 1 GB / 5 million source rows; processed in batches. NSE
          CM EQ records are imported and other series are counted as excluded.
          Instruments are created for historical research automatically. Stop
          live paper streaming and replay before importing. Raw prices are not
          adjusted for corporate actions.
        </p>
      )}
      {report && (
        <div className="notice">
          Imported {report.rows} candles. {report.warnings.length} quality
          warnings. Dataset SHA-256: <code>{report.sha256}</code>
          {report.warnings.length > 0 && (
            <details>
              <summary>Review warnings</summary>
              <pre>{JSON.stringify(report.warnings, null, 2)}</pre>
            </details>
          )}
        </div>
      )}
      <div className="section-head">
        <h3>Datasets</h3>
        <button
          className="quiet"
          onClick={() => act(async () => setQuality(await api("/quality")))}
        >
          Import audit
        </button>
      </div>
      <Table
        rows={datasets}
        columns={[
          { key: "name", label: "Dataset" },
          { key: "source", label: "Source" },
          { key: "timeframe", label: "Interval" },
          { key: "rows", label: "Candles" },
          { key: "first", label: "From", render: (r) => when(r.first) },
          { key: "last", label: "Through", render: (r) => when(r.last) },
          {
            key: "delete",
            label: "",
            render: (r) => (
              <button
                className="icon danger"
                title="Delete unreferenced dataset"
                onClick={() => {
                  if (
                    confirm(
                      "Delete this dataset? Referenced experiment data cannot be deleted.",
                    )
                  )
                    act(async () => {
                      await api("/datasets/" + r.id, "DELETE");
                      await refresh();
                    });
                }}
              >
                <Trash2 size={15} />
              </button>
            ),
          },
        ]}
        empty="No imported datasets"
      />
      {quality.length > 0 && (
        <Table
          rows={quality}
          columns={[
            { key: "filename", label: "Import" },
            { key: "status", label: "Status" },
            { key: "details", label: "Findings" },
          ]}
        />
      )}
      <h3 className="space-top">Instrument registry</h3>
      <Table
        rows={instruments}
        columns={[
          { key: "symbol", label: "Symbol" },
          { key: "kind", label: "Type" },
          { key: "lot_size", label: "Lot size" },
          { key: "tick_paise", label: "Tick (paise)" },
          { key: "expiry", label: "Expiry" },
          { key: "metadata_source", label: "Metadata source" },
        ]}
        empty="Register instruments before importing candles"
      />
      {modal && (
        <Modal title="Register instrument" close={() => setModal(false)}>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              act(async () => {
                await api("/instruments", "POST", {
                  symbol: f.get("symbol"),
                  kind,
                  lot_size: Number(f.get("lot_size")),
                  tick_size: Number(f.get("tick_size")),
                  metadata_source: f.get("metadata_source"),
                  provider_key: f.get("provider_key") || null,
                  expiry: f.get("expiry") || null,
                  strike: f.get("strike") ? Number(f.get("strike")) : null,
                  underlying: f.get("underlying") || null,
                  settlement: f.get("settlement") || "CASH",
                });
                await refresh();
                setModal(false);
              });
            }}
          >
            <label>
              Symbol
              <input name="symbol" required placeholder="e.g. RELIANCE" />
            </label>
            <label>
              Type
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                {["SPOT", "INDEX", "FUTURE", "CALL", "PUT"].map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
            </label>
            <label>
              Lot size
              <input
                name="lot_size"
                type="number"
                min="1"
                defaultValue="1"
                required
              />
            </label>
            <label>
              Tick size (INR)
              <input
                name="tick_size"
                type="number"
                min="0.01"
                step="0.01"
                defaultValue="0.05"
                required
              />
            </label>
            <label className="full">
              Metadata source
              <input
                name="metadata_source"
                required
                placeholder="Exchange contract file, date or source reference"
              />
            </label>
            <label className="full">
              Upstox instrument key (optional)
              <input name="provider_key" placeholder="NSE_EQ|..." />
            </label>
            {["CALL", "PUT", "FUTURE"].includes(kind) && (
              <>
                <label>
                  Expiry
                  <input type="date" name="expiry" required />
                </label>
                <label>
                  Settlement
                  <select name="settlement">
                    <option>CASH</option>
                    <option>PHYSICAL</option>
                  </select>
                </label>
                <label>
                  Strike (INR)
                  <input
                    type="number"
                    step="0.01"
                    name="strike"
                    required={kind !== "FUTURE"}
                  />
                </label>
                <label>
                  Underlying
                  <input name="underlying" required={kind !== "FUTURE"} />
                </label>
              </>
            )}
            <button className="primary full">Register</button>
          </form>
        </Modal>
      )}
    </>
  );
}
