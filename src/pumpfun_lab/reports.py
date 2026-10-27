"""Local report rendering; all user-controlled content is escaped."""

from html import escape
import json
from pathlib import Path


def render_html(title: str, markdown: str) -> str:
    """Render readable source text without interpreting user HTML or scripts."""
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<title>""" + escape(title) + """</title><style>
:root{color-scheme:light;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#182d3a;background:#edf3f5}
body{margin:0}main{max-width:1100px;margin:32px auto;padding:30px;background:#fff;border:1px solid #d5e0e4;border-radius:16px}
header{border-bottom:3px solid #138579;margin-bottom:24px;padding-bottom:18px}
h1{font-size:30px;margin:12px 0}.badge{display:inline-block;background:#fff0ca;color:#644900;padding:6px 10px;border-radius:20px;font-size:12px;font-weight:700;letter-spacing:.06em}
p{line-height:1.55}pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;font-size:14px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
footer{border-top:1px solid #d5e0e4;margin-top:25px;padding-top:15px;color:#556b77;font-size:13px}
@media(max-width:700px){main{margin:0;padding:18px;border:0;border-radius:0}h1{font-size:24px}pre{font-size:12px}}
@media print{body{background:#fff}main{margin:0;padding:0;border:0}pre{font-size:9pt}}
</style></head><body><main><header><span class="badge">PAPER RESEARCH · NO LIVE ORDERS</span>
<h1>""" + escape(title) + """</h1><p>Source labels and unresolved data are retained. Synthetic examples test software behavior; they do not establish trading profitability.</p></header>
<pre>""" + escape(markdown) + """</pre><footer>Generated locally by Pump.fun Lab. No scripts, tracking, external resources, or wallet connection.</footer></main></body></html>
"""


def write_report(output: Path, report: dict, markdown: str, title: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "report.md").write_text(markdown, encoding="utf-8")
    (output / "report.html").write_text(render_html(title, markdown), encoding="utf-8")


def replay_markdown(results: list[dict]) -> str:
    lines = [
        "# Paper replay comparison", "",
        "A synthetic dataset is an engineering fixture, not evidence of trading profitability.",
        "Results on other data remain conditional on its completeness and the execution model.", "",
        "| Strategy | Source | Closed | Net simulated SOL* | Max drawdown % | Open positions |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in results:
        source = str(row["source_label"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['strategy']} | {escape(source)} | {row['closed_trade_count']} | "
            f"{float(row['net_pnl_sol']):+.8f} | {float(row['max_drawdown_pct']):.4f} | "
            f"{len(row['open_positions'])} |"
        )
    lines += ["", "*Net simulated SOL = final cash + conservative inventory value - initial virtual cash.",
              "Unresolved holdings are not reported as completed trades. Stale or unsellable inventory is marked conservatively.", ""]
    for row in results:
        lines += [f"## {row['strategy']}", "", "### Assumptions", "",
                  "```json", json.dumps(row["parameters"], indent=2, ensure_ascii=False), "```", "",
                  "### Execution limitations", ""]
        assumptions = row.get("assumptions", [])
        if isinstance(assumptions, list):
            lines.extend(f"- {escape(str(a))}" for a in assumptions)
        else:
            lines.extend(["```json", json.dumps(assumptions, indent=2), "```"])
        lines += ["", f"Rejected/deferred actions recorded: {len(row['rejections'])}.",
                  f"Pending orders at end: {len(row['pending_orders'])}.",
                  "Full fills, closed trades, unresolved inventory, costs and equity curve are in report.json.", ""]
    return "\n".join(lines)
