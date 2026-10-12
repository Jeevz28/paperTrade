import { test, expect } from "@playwright/test";

test("empty application renders truthfully at desktop and mobile", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://127.0.0.1:8000");
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("No market data loaded")).toBeVisible();
  await expect(page.getByText("No open paper positions")).toBeVisible();
  await page.screenshot({ path: "../artifacts/desktop.png", fullPage: true });
  await page
    .getByRole("button", { name: "Data Management", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Data workspace" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Register instrument", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await page.screenshot({ path: "../artifacts/mobile.png", fullPage: true });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});

test("activity workspace empty-state controls and tabs", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://127.0.0.1:8000");
  await page
    .getByRole("button", { name: "Activity & Automation", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Activity & automation", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start stream", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Enable paper strategy", exact: true }),
  ).toBeDisabled();
  for (const tab of [
    "Orders",
    "Baskets",
    "Decisions",
    "Deliveries",
    "Backtests",
    "Timeline",
  ])
    await page.getByRole("button", { name: tab, exact: true }).click();
  await page.screenshot({
    path: "../artifacts/activity-empty.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});

test("derivative evidence and backup controls render without errors", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://127.0.0.1:8000");
  await page
    .getByRole("button", { name: "Options / F&O", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Atomic replay execution" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Queue basket" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("heading", { name: "Import point-in-time evidence" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../artifacts/derivatives.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const downloaded = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download verified database backup" })
    .click();
  expect((await downloaded).suggestedFilename()).toBe("paperlab-backup.sqlite");
  expect(errors).toEqual([]);
});

test("TEST DATA ONLY: import, replay, paper fill and ledger", async ({
  page,
  request,
}) => {
  const headers = { "X-PaperLab": "local" };
  const instrument = await request.post(
    "http://127.0.0.1:8000/api/instruments",
    {
      headers,
      data: {
        symbol: "UI_TEST_ONLY",
        tick_size: 0.01,
        metadata_source: "SYNTHETIC SOFTWARE TEST ONLY",
      },
    },
  );
  expect(instrument.ok()).toBeTruthy();
  const instrumentId = (await instrument.json()).id;
  const csv =
    "timestamp,symbol,open,high,low,close,volume\n" +
    Array.from({ length: 65 }, (_, i) => {
      const time = new Date(Date.UTC(2024, 0, 2, 3, 45 + i * 5)).toISOString();
      const price = 100 + (i % 10) * 0.1;
      return [
        time,
        "UI_TEST_ONLY",
        price,
        (price + 1).toFixed(2),
        (price - 1).toFixed(2),
        price,
        1000,
      ].join(",");
    }).join("\n");
  await page.goto("http://127.0.0.1:8000");
  await page
    .getByRole("button", { name: "Data Management", exact: true })
    .click();
  await page.locator("input[type=file]").setInputFiles({
    name: "UI_TEST_ONLY.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv),
  });
  await page
    .getByLabel("Data source / permission")
    .fill("SYNTHETIC SOFTWARE TEST ONLY");
  await page.getByRole("button", { name: "Validate & import" }).click();
  await expect(
    page.getByText("Imported 65 candles.", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Create new paper account" }).click();
  await page.getByLabel("Experiment name").fill("UI TEST ONLY");
  await page
    .getByRole("dialog")
    .locator('select[name="dataset"]')
    .selectOption({ label: "UI_TEST_ONLY.csv" });
  await page
    .getByRole("button", { name: "Create paper account", exact: true })
    .click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page
    .getByRole("button", { name: "Historical Replay", exact: true })
    .click();
  await page.getByRole("button", { name: "Next candle" }).click();
  await page
    .getByRole("img", { name: "Imported OHLC candles, indicators and volume" })
    .waitFor();
  await page.getByLabel("Quantity", { exact: true }).fill("1");
  await page.getByLabel("Stop (INR)", { exact: true }).fill("99");
  await page.getByRole("button", { name: "Submit paper order" }).click();
  await expect(page.getByText("PENDING", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Next candle" }).click();
  await expect(page.getByText("FILLED", { exact: true })).toBeVisible();
  const accounts = await (
    await request.get("http://127.0.0.1:8000/api/accounts")
  ).json();
  const account = await (
    await request.get("http://127.0.0.1:8000/api/accounts/" + accounts[0].id)
  ).json();
  expect(account.initial_capital).toBe(100000);
  expect(account.cash_balance).toBe(89990);
  expect(account.positions[0].instrument_id).toBe(instrumentId);
  expect(account.reconciliation.ok).toBeTruthy();
  await page.screenshot({
    path: "../artifacts/replay-test-only.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Account Ledger", exact: true })
    .click();
  await expect(
    page.getByRole("cell", { name: "PAPER_BUY", exact: true }),
  ).toBeVisible();
  const ds = (
    await (await request.get("http://127.0.0.1:8000/api/datasets")).json()
  )[0];
  const response = await request.post("http://127.0.0.1:8000/api/accounts", {
    headers,
    data: { name: "UI_AUTOMATION_TEST_ONLY", dataset_id: ds.id },
  });
  const aid = (await response.json()).id;
  await page.reload();
  await page
    .getByRole("button", { name: "Activity & Automation", exact: true })
    .click();
  await page
    .getByLabel("Paper account", { exact: true })
    .selectOption(String(aid));
  const automation = page.getByRole("region", {
    name: "Automatic strategy execution",
    exact: true,
  });
  await automation
    .getByRole("combobox", { name: "Contract A", exact: true })
    .selectOption(String(instrumentId));
  await automation
    .getByRole("combobox", { name: "Strategy", exact: true })
    .selectOption("adaptive");
  await automation
    .getByRole("button", { name: "Enable paper strategy", exact: true })
    .click();
  await expect(
    automation.getByRole("cell", { name: "Enabled", exact: true }),
  ).toBeVisible();
  await automation
    .getByRole("button", { name: "Start background replay", exact: true })
    .click();
  await page.getByRole("button", { name: "Decisions", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "WARMUP", exact: true }).first(),
  ).toBeVisible();
  await expect(
    automation.getByRole("heading", { name: "Adaptive strategy monitor" }),
  ).toBeVisible();
  await expect(
    automation.getByText("Adaptive selection requires 200 completed bars.", {
      exact: true,
    }),
  ).toBeVisible();
  const rules = await (
    await request.get("http://127.0.0.1:8000/api/automation")
  ).json();
  const adaptive = rules.find((r: any) => r.account_id === aid);
  expect(adaptive.strategy).toBe("adaptive");
  expect(adaptive.adaptive.selected).toBeNull();
  expect(adaptive.adaptive.required).toBe(200);
  await automation
    .getByRole("button", { name: "Pause background replay", exact: true })
    .click();
  await automation
    .getByRole("button", {
      name: "Stop strategy & cancel its pending entries",
      exact: true,
    })
    .click();
  await expect(
    automation.getByRole("cell", { name: "Stopped", exact: true }),
  ).toBeVisible();
  await automation.screenshot({
    path: "../artifacts/automation-test-only.png",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("NSE learning import and honest no-qualifying-profile workflow", async ({
  page,
  request,
}) => {
  test.setTimeout(60000);
  const headers = { "X-PaperLab": "local" };
  const head =
    "TradDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol";
  const lines = Array.from({ length: 610 }, (_, i) => {
    const d = new Date(Date.UTC(2022, 0, 1 + i)).toISOString().slice(0, 10);
    return `${d},CM,NSE,STK,123,INE000A01010,UI_LEARNING_TEST,EQ,100,101,99,100,1000`;
  });
  await page.goto("http://127.0.0.1:8000");
  await page
    .getByRole("button", { name: "Data Management", exact: true })
    .click();
  await page.getByRole("combobox", {name: "File format", exact: true}).selectOption("nse");
  await page
    .getByLabel("Dataset", { exact: true })
    .setInputFiles({
      name: "NSE_LEARNING_TEST.csv",
      mimeType: "text/csv",
      buffer: Buffer.from([head, ...lines].join("\n")),
    });
  await page
    .getByLabel("Data source / permission")
    .fill("SYNTHETIC SOFTWARE TEST ONLY");
  await page.getByRole("button", { name: "Validate & import" }).click();
  await expect(
    page.getByText("Imported 610 candles.", { exact: false }),
  ).toBeVisible();
  const ds = (
    await (await request.get("http://127.0.0.1:8000/api/datasets")).json()
  ).find((d: any) => d.name === "NSE_LEARNING_TEST.csv");
  const response = await request.post("http://127.0.0.1:8000/api/accounts", {
    headers,
    data: {
      name: "LEARNING_UI_TEST",
      dataset_id: ds.id,
      costs: { brokerage_paise: 1, slippage_bps: 1 },
    },
  });
  const aid = (await response.json()).id;
  await page.reload();
  await page
    .getByRole("button", { name: "Activity & Automation", exact: true })
    .click();
  await page
    .getByLabel("Paper account", { exact: true })
    .selectOption(String(aid));
  const panel = page.getByRole("region", { name: "CSV strategy learning" });
  await panel.getByRole("combobox", {name: "Learning dataset", exact: true}).selectOption(String(ds.id));
  const sec = await (
    await request.get(
      `http://127.0.0.1:8000/api/learning/securities?dataset_id=${ds.id}`,
    )
  ).json();
  await expect(
    panel.getByRole("combobox", {name: "Learning security", exact: true}).locator("option"),
  ).toHaveCount(2);
  await panel
    .getByRole("combobox", {name: "Learning security", exact: true})
    .selectOption(String(sec[0].instrument_id));
  await panel
    .getByRole("button", { name: "Study and validate strategies" })
    .click();
  await expect(
    panel.getByRole("cell", { name: "No qualifying settings", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await panel.getByRole("button", { name: /Review profile/ }).click();
  await expect(panel.getByRole("paragraph").filter({hasText: "Disjoint chronological 50%"})).toBeVisible();
  await panel.screenshot({ path: "../artifacts/learning-test-only.png" });
});


test('automatic paper setup explains costs, capital and scan status', async ({page})=>{
  const errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8000');
  await page.getByRole('button',{name:'Auto Paper',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Automatic paper trading',exact:true})).toBeVisible();
  await expect(page.getByLabel('Capital (INR)',{exact:true})).toHaveValue('5000');
  await expect(page.getByRole('button',{name:'Start automatic paper trading',exact:true})).toBeDisabled();
  await expect(page.getByText('No paper orders yet.',{exact:false})).toBeVisible();
  await page.screenshot({path:'../artifacts/auto-paper.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});

test('SYNTHETIC large universe shows active and queued coverage',async({page})=>{
  await page.route('**/api/auto-paper',async route=>route.fulfill({json:{
    sessions:[{account_id:991,name:'SYNTHETIC BATCH TEST',initial_capital:500000,enabled:1,state:{}}],
    stocks:Array.from({length:2597},(_,i)=>({instrument_id:i,ticker:'TEST'+i,status:i<2000?'WARMING_UP':'QUEUED_FOR_NEXT_BATCH',detail:{}})),
    orders:[],stream:{state:'CONNECTED',accounts:[991],subscribed_count:2000,queued_count:597,universe_count:2597,rotation_after_market_minutes:220,snapshots:[]}
  }}));
  await page.goto('http://127.0.0.1:8000');
  await page.getByRole('button',{name:'Auto Paper',exact:true}).click();
  await expect(page.getByText('Batch coverage: 2000 active / 597 queued / 2597 total')).toBeVisible();
  await page.getByLabel('Find stock or status').fill('QUEUED');
  await expect(page.getByRole('cell',{name:'QUEUED FOR NEXT BATCH',exact:true})).toHaveCount(597);
});

test('F&O home defaults to index and creates a persistent separate managed account',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8000');
  await expect(page.getByRole('heading',{name:'Automated F&O trading',exact:true})).toBeVisible();
  await expect(page.getByText('This trades contracts on the NIFTY 50 index',{exact:false})).toBeVisible();
  await expect(page.getByLabel('Initial virtual capital (INR)')).toHaveValue('5000');
  await page.getByRole('button',{name:'Create NIFTY account',exact:true}).click();
  await expect(page.getByLabel('F&O paper account')).toContainText('NIFTY 50 index F&O · DEFAULT');
  await expect(page.getByRole('button',{name:'Start automated F&O',exact:true})).toBeEnabled();
  await expect(page.getByRole('region',{name:'Open F&O positions'})).toBeVisible();
  await expect(page.getByRole('region',{name:'F&O orders and fills'})).toBeVisible();
  await page.screenshot({path:'../artifacts/fno-home.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  await page.screenshot({path:'../artifacts/fno-mobile.png',fullPage:true});
  expect(errors).toEqual([]);
});

test('SYNTHETIC F&O fills and live positions are visible without navigating away',async({page})=>{
  await page.route('**/api/fno-auto',async route=>route.fulfill({json:{
    selected_account:999,sessions:[{account_id:999,label:'NIFTY 50 index F&O',market:'NIFTY',initial_capital:10000000,enabled:1,state:{status:'OPEN',message:'SYNTHETIC SOFTWARE TEST — not a market result',strategy:'trend'}}],
    snapshot:{equity_paise:10010000,available_cash_paise:9000000,realized_pnl:0,costs:2500,basket_positions:[{id:1,basket_id:1,contract:'SYNTHETIC NIFTY CALL',kind:'CALL',expiry:'2026-10-29',signed_quantity:10,basis_price:10000,mark_paise:11000,unrealized_pnl_paise:10000,stop_paise:7500,target_paise:15000,status:'OPEN'}]},
    orders:[{id:1,contract:'SYNTHETIC NIFTY CALL',signal:'trend',quantity:10,status:'OPEN',created_at:'2026-10-12T04:30:00+00:00',entry_paise:10000,exit_paise:null,net_pnl:null}],events:[],contracts:[],stream:{state:'CONNECTED'}
  }}));
  await page.goto('http://127.0.0.1:8000');
  await expect(page.getByRole('cell',{name:'FILLED / OPEN',exact:true})).toBeVisible();
  await expect(page.getByRole('region',{name:'Open F&O positions'}).getByText('SYNTHETIC NIFTY CALL',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Close paper position & pause',exact:true})).toBeEnabled();
  await page.screenshot({path:'../artifacts/fno-synthetic-position.png',fullPage:true});
});
