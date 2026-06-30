"""
rich terminal dashboard.

Renders: account equity + day P&L, open positions with P&L and stop/target
distance, allocation, today's actions, and any alerts.
"""
from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from src.alerts import Alert
from src.analytics import PortfolioView
from src.broker import AccountSummary
from src.context import MacroContext
from src.execution import ExecutionResult

console = Console()


def _pnl_style(x: float) -> str:
    return "green" if x > 0 else "red" if x < 0 else "white"


def render_dashboard(
    account: AccountSummary,
    portfolio: PortfolioView,
    macro: MacroContext,
    market_summary: str,
    exec_results: list[ExecutionResult],
    rejected: list[Any],
    alerts: list[Alert],
    halt_triggered: bool,
) -> None:
    console.rule("[bold cyan]Alpaca Paper Trading Bot — Morning Run")

    # --- account header ---
    header = Table.grid(expand=True)
    header.add_column(justify="left")
    header.add_column(justify="right")
    day_style = _pnl_style(account.day_pnl)
    header.add_row(
        f"[bold]Equity[/bold] ${account.equity:,.2f}",
        f"[bold]Day P&L[/bold] [{day_style}]{account.day_pnl:+,.2f} "
        f"({account.day_pnl_pct:+.2%})[/{day_style}]",
    )
    header.add_row(
        f"Cash ${account.cash:,.2f}  •  Buying power ${account.buying_power:,.2f}",
        f"VIX {macro.vix} ({macro.regime})",
    )
    mode = "[bold red]HALTED[/bold red]" if halt_triggered else "[green]ACTIVE[/green]"
    header.add_row(f"Account {account.account_number} (PAPER)", f"Status: {mode}")
    console.print(Panel(header, title="Account", border_style="cyan"))

    # --- positions ---
    if portfolio.positions:
        pt = Table(title="Open Positions", box=box.SIMPLE_HEAVY, expand=True)
        for col in ("Ticker", "Qty", "Entry", "Last", "Mkt Val", "uP&L",
                    "uP&L %", "Wt", "→Stop", "→Target"):
            pt.add_column(col, justify="right")
        pt.columns[0].justify = "left"
        for p in portfolio.positions:
            s = _pnl_style(p.unrealized_pl)
            pt.add_row(
                p.ticker, f"{p.qty:g}", f"{p.avg_entry:.2f}", f"{p.last_price:.2f}",
                f"{p.market_value:,.0f}",
                f"[{s}]{p.unrealized_pl:+,.0f}[/{s}]",
                f"[{s}]{p.unrealized_pl_pct:+.1%}[/{s}]",
                f"{p.weight:.0%}",
                f"{p.dist_to_stop_pct:+.1%}", f"{p.dist_to_target_pct:+.1%}",
            )
        console.print(pt)
    else:
        console.print(Panel("[dim]No open positions.[/dim]", title="Open Positions"))

    # --- allocation ---
    alloc = Table.grid(expand=True)
    alloc.add_column(justify="left")
    alloc.add_row(
        f"Invested {portfolio.invested_pct:.0%} (${portfolio.invested:,.0f})  •  "
        f"Cash {portfolio.cash_pct:.0%} (${portfolio.cash:,.0f})  •  "
        f"Positions {portfolio.num_positions}  •  "
        f"Largest {portfolio.largest_position_weight:.0%}  •  "
        f"Total uP&L [{_pnl_style(portfolio.total_unrealized_pl)}]"
        f"{portfolio.total_unrealized_pl:+,.0f}[/]"
    )
    console.print(Panel(alloc, title="Allocation", border_style="blue"))

    # --- today's actions ---
    at = Table(title="Today's Actions", box=box.SIMPLE_HEAVY, expand=True)
    for col in ("Action", "Ticker", "Qty", "Status", "Detail"):
        at.add_column(col, justify="left")
    if exec_results:
        for r in exec_results:
            status_style = {"placed": "green", "skipped": "yellow",
                            "error": "red", "rejected": "red"}.get(r.status, "white")
            at.add_row(r.action.upper(), r.ticker, f"{r.qty:g}",
                       f"[{status_style}]{r.status}[/{status_style}]",
                       (r.detail or "")[:60])
    for rj in rejected:
        at.add_row(getattr(rj, "action", "?").upper(), getattr(rj, "ticker", "?"),
                   "-", "[dim]not sized[/dim]", getattr(rj, "reason", "")[:60])
    if not exec_results and not rejected:
        at.add_row("[dim]none[/dim]", "", "", "", "no trades today")
    console.print(at)

    # --- market summary ---
    console.print(Panel(market_summary or "[dim]n/a[/dim]",
                        title="Market Summary (LLM)", border_style="magenta"))

    # --- alerts ---
    if alerts:
        atbl = Table(title="Alerts", box=box.SIMPLE, expand=True)
        atbl.add_column("Level"); atbl.add_column("Message")
        for a in alerts:
            style = {"critical": "bold red", "warning": "yellow", "info": "cyan"}.get(a.level, "white")
            atbl.add_row(f"[{style}]{a.level.upper()}[/{style}]", a.message)
        console.print(atbl)

    console.rule("[bold cyan]End of run")
