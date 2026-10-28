# Tidewatch forecast scorecard (public, append-only)

Daily GPU rental price index + 1/7/30-day forecasts for H100, H200, B200, A100, L40S, RTX 4090, built **only from public data**
(Vast.ai live offers, RunPod public GraphQL, Lambda pricing page, AWS public price JSON, Azure Retail Prices API; OpenRouter as demand context).

**Live track record started 2026-10-28 (Day 1 of 30).** No earlier live days exist and none will be back-filled.

How a day works (`pipeline.py`, run by `.github/workflows/daily.yml` at 01:30 UTC / 09:30 SGT):
1. Snapshot all sources → `data/snapshots/YYYY-MM-DD.json` (raw quotes, per-source status).
2. Index per class = weighted median of source quotes (hyperscaler list prices weight 0.5; any single source capped at 35%).
3. Anomaly screen (rule-based v0): source outliers (>50% from median), day-over-day jumps (>20%), Vast supply drops, single-source classes.
4. Reveal & score: every earlier forecast whose target date is today is scored against today's index → `scores/scored.jsonl`.
5. Issue today's forecast → `forecasts/YYYY-MM-DD.json` (canonical JSON), SHA-256 appended to `ledger.jsonl`, timestamped with
   OpenTimestamps (`.ots.b64`, Bitcoin-anchored, verify with `ots verify`). Git history is the append-only log.
6. BSC testnet commit: if secret `BSC_TESTNET_PK` (a throwaway, funded **testnet** key) is set, the SHA-256 is written as calldata of a 0-value self-transaction on chainId 97. **Currently pending** — the faucet needs a human.

Forecasts are published in clear before their outcome is known (commit = the published hash + timestamps; reveal = scoring on the target date).

Model: champion = naive (last index), because it beat damped Holt in the walk-forward backtest; 80% intervals from backtest error quantiles with floors ±2/4/8%.
Backtest (`backtest.py`, `backtest.json`) is labelled **BACKTEST**: walk-forward on Wayback-archived public list prices (Lambda, RunPod), no leakage.

Verify a day: `python -c "import hashlib;print(hashlib.sha256(open('forecasts/2026-10-28.json','rb').read()).hexdigest())"` and compare with `ledger.jsonl`.
Not investment advice; listed prices are not transaction prices.
