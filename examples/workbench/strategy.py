"""Deterministic artifact-contract demo with synthetic prices; no network or models."""
import argparse
import csv
import json
import math
import os
import statistics
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--shares", type=int, default=5)
args = parser.parse_args()
if not 0 <= args.shares <= 10:
    parser.error("shares must be between 0 and 10")
run = Path(os.environ["RUN_DIR"])
config = json.loads((run / "config.json").read_text())
initial = float(config.get("initial_cash", 1000))
with Path(__file__).with_name("prices.csv").open() as stream:
    prices = list(csv.DictReader(stream))
entry = float(prices[0]["close"])
cash = initial - args.shares * entry
peak = previous = initial
rows, returns = [], []
for bar in prices:
    equity = cash + args.shares * float(bar["close"])
    ret = equity / previous - 1
    peak = max(peak, equity)
    rows.append({"timestamp":bar["timestamp"],"equity":equity,"ret":ret,"drawdown":equity/peak-1,"cash":cash})
    returns.append(ret)
    previous = equity
out = run / "artifacts"
out.mkdir(exist_ok=True)
def save(name, columns, values):
    with (out / name).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(values)
save("equity.csv", ["timestamp","equity","ret","drawdown","cash"], rows)
pnl = args.shares * (float(prices[-1]["close"]) - entry)
trades = [] if not args.shares else [
    dict(timestamp=prices[0]["timestamp"],code="DEMO",side="buy",pnl=0,reason="synthetic entry",return_pct=0),
    dict(timestamp=prices[-1]["timestamp"],code="DEMO",side="sell",pnl=pnl,reason="synthetic exit",return_pct=float(prices[-1]["close"])/entry-1),
]
save("trades.csv", ["timestamp","code","side","pnl","reason","return_pct"], trades)
vol = statistics.stdev(returns[1:])
metrics = dict(final_value=previous,total_return=previous/initial-1,annual_return=(previous/initial)**(252/(len(prices)-1))-1,sharpe=statistics.mean(returns[1:])/vol*math.sqrt(252) if vol else 0,max_drawdown=min(row["drawdown"] for row in rows),win_rate=float(pnl>0) if trades else 0,trade_count=len(trades))
save("metrics.csv", list(metrics), [metrics])
print(json.dumps({"message":"Synthetic offline demo complete","shares":args.shares,"metrics":metrics}), flush=True)
