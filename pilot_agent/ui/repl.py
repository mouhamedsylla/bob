"""
Interface REPL terminal pour pilot-agent.

Principes d'affichage :
- Une seule ligne compacte par tool call (✓ nom args  durée)
- Spinner transient pendant l'exécution — disparaît proprement
- Réponse finale en Markdown, largeur bornée
- Credentials masqués inline, approbations inline
"""
from __future__ import annotations

import asyncio
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style
from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from pilot_agent.gates.approval import ApprovalGate, ApprovalRequest
from pilot_agent.llm.provider import LLMProvider, Message
from pilot_agent.loop.agent import AgentCallbacks, run
from pilot_agent.mcp.client import MCPHub

console = Console()

PT_STYLE = Style.from_dict({
    "prompt":      "#61afef bold",
    "prompt-icon": "#98c379",
})


# ── Bannière statique ────────────────────────────────────────────────────────

def print_banner(model_id: str, project_name: str, active_env: str) -> None:
    """
    Bannière de démarrage bob — inspirée Pilot Fox.

    ╭────────────────────────────────────────────────────────╮
    │  ╭─╮╭─╮                                               │
    │  ╰─╯╰─╯  bob  v0.1                                    │
    │  █ ▘▝ █  claude-3-5-sonnet  ·  mon-projet  ·  prod    │
    │                                                        │
    │  Décris ce que tu veux faire  ·  Ctrl+D  ·  Ctrl+C    │
    ╰────────────────────────────────────────────────────────╯
    """
    C = 56          # largeur du contenu (entre les │)
    O = "color(208)"  # orange xterm-256
    D = "dim"

    def row(*segments: tuple[str, str]) -> Text:
        """Ligne complète : │<contenu paddé à C chars>│"""
        t = Text()
        t.append("│", style=D)
        body = Text()
        for txt, sty in segments:
            body.append(txt, style=sty)
        pad = C - len(body.plain)
        if pad > 0:
            body.append(" " * pad)
        t.append_text(body)
        t.append("│", style=D)
        return t

    # Infos contextuelles — supprime le préfixe provider (ex: "anthropic/")
    short_model = model_id.split("/")[-1] if "/" in model_id else model_id
    info = f"{short_model}  ·  {project_name}  ·  {active_env}"
    max_info = C - 11   # 11 = len("  █ ▘▝ █  ")
    if len(info) > max_info:
        info = info[:max_info - 1] + "…"

    console.print()
    console.print(Text("  ╭" + "─" * C + "╮", style=D))
    console.print(Text("  ") + row(("  ╭─╮╭─╮", O)))
    console.print(Text("  ") + row(("  ╰─╯╰─╯  ", O), ("bob", "bold white"), ("  v0.1", D)))
    console.print(Text("  ") + row(("  █ ▘▝ █  ", O), (info, D)))
    console.print(Text("  ") + row())
    console.print(Text("  ") + row(("  Décris ce que tu veux faire  ·  Ctrl+D  ·  Ctrl+C", D)))
    console.print(Text("  ╰" + "─" * C + "╯", style=D))
    console.print()


# ── Callbacks d'affichage ─────────────────────────────────────────────────────

class ReplCallbacks:
    """
    Chaque tool call produit UNE seule ligne :
      ✓  pilot_preflight (env='prod')  3.9s

    Le spinner est transient — il disparaît quand le résultat arrive.
    """

    def __init__(self) -> None:
        self._live: Live | None = None
        self._current_args_preview: str = ""

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _start_spinner(self, label: str) -> None:
        self._stop_live()
        spinner = Spinner("dots", text=f" {label}", style="cyan")
        self._live = Live(
            spinner, console=console, refresh_per_second=15, transient=True
        )
        self._live.start()

    def _stop_live(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    # ── Thinking ──────────────────────────────────────────────────────────────

    def on_thinking(self) -> None:
        self._start_spinner("[dim]…[/]")

    # ── Tool calls — UNE ligne par appel ──────────────────────────────────────

    def on_tool_call(self, name: str, args: dict) -> None:
        """Lance le spinner transient pendant l'exécution."""
        self._current_args_preview = _fmt_args(args)
        label = f"[bold]{name}[/][dim]{self._current_args_preview}[/]"
        self._start_spinner(label)

    def on_tool_result(self, name: str, result: str, elapsed_ms: float) -> None:
        """Arrête le spinner et imprime la ligne de résultat compacte."""
        self._stop_live()
        duration = (
            f"{elapsed_ms:.0f}ms" if elapsed_ms < 1000 else f"{elapsed_ms / 1000:.1f}s"
        )
        console.print(
            f"  [green]✓[/]  [bold]{name}[/][dim]{self._current_args_preview}[/]"
            f"  [dim]{duration}[/]",
            highlight=False,
        )

    def on_tool_denied(self, name: str) -> None:
        self._stop_live()
        console.print(f"  [yellow]—[/]  [dim]{name} annulé[/]", highlight=False)

    # ── Réponse finale ────────────────────────────────────────────────────────

    def on_done(self, result: str) -> None:
        self._stop_live()
        result = result.strip()
        if not result:
            return

        console.print()

        # Réponse courte et sans markdown → impression directe, sans panel
        if len(result) < 120 and "\n" not in result and not _has_markdown(result):
            console.print(f"  {result}\n")
            return

        # Réponse longue ou structurée → panel Markdown borné à 96 colonnes
        width = min(96, max(60, console.width - 4))
        console.print(
            Panel(
                Markdown(result),
                border_style="bright_black",
                box=box.ROUNDED,
                padding=(0, 2),
                width=width,
            )
        )
        console.print()

    def on_error(self, error: str) -> None:
        self._stop_live()
        console.print(f"\n  [red]✗[/]  {error}\n")

    # ── Credential inline ─────────────────────────────────────────────────────

    async def on_collect_credential(self, key: str, prompt: str, secret: bool) -> str:
        self._stop_live()

        icon = "🔑" if secret else "📝"
        width = min(64, console.width - 4)

        console.print(
            Panel(
                Text.assemble(
                    (f"{icon}  ", ""),
                    (prompt, "bold cyan"),
                    ("  [dim]→ ", ""),
                    (key, "dim"),
                    ("[/]", ""),
                ),
                border_style="yellow",
                box=box.ROUNDED,
                width=width,
                padding=(0, 1),
            )
        )

        if secret:
            from prompt_toolkit import prompt as pt_prompt
            value = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: pt_prompt(
                    HTML(f"  <ansiyellow>❯</ansiyellow> "),
                    is_password=True,
                ),
            )
        else:
            value = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("  ❯  ")
            )

        value = (value or "").strip()
        if value:
            console.print(f"  [green]✓[/]  [dim]{key}[/]\n")
        else:
            console.print(f"  [dim]annulé[/]\n")
        return value


# ── Gate confirmation ─────────────────────────────────────────────────────────

class ReplApprovalGate:

    async def request(self, req: ApprovalRequest) -> bool:
        args_str = _fmt_args(req.arguments).strip(" ()") or "—"
        width = min(72, console.width - 4)

        console.print(
            Panel(
                Text.assemble(
                    ("⚠  ", "bold yellow"),
                    (req.tool_name, "bold cyan"),
                    (f"  {args_str}\n\n", "dim"),
                    (f"  {req.reason}", "dim italic"),
                ),
                border_style="yellow",
                box=box.ROUNDED,
                width=width,
                padding=(0, 1),
            )
        )
        answer = await asyncio.get_event_loop().run_in_executor(
            None, lambda: input("  Confirmer ? [o/N]  ").strip().lower()
        )
        approved = answer in ("o", "oui", "y", "yes")
        console.print(
            f"  [green]✓ confirmé[/]\n" if approved else f"  [dim]annulé[/]\n"
        )
        return approved


# ── REPL principal ────────────────────────────────────────────────────────────

async def start_repl(
    provider: LLMProvider,
    pilot_client: MCPHub,
    project_name: str = "projet",
    active_env: str = "dev",
    max_steps: int = 20,
) -> None:
    print_banner(provider.model_id, project_name, active_env)

    session: PromptSession = PromptSession(
        history=InMemoryHistory(), style=PT_STYLE
    )

    history: list[Message] = []
    gate = ReplApprovalGate()
    cb = ReplCallbacks()

    while True:
        try:
            goal = await session.prompt_async(
                HTML("<prompt-icon>❯</prompt-icon> <prompt> </prompt>"),
                style=PT_STYLE,
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n  [dim]À bientôt.[/]\n")
            break

        goal = goal.strip()
        if not goal:
            continue
        if goal.lower() in ("exit", "quit", "q", "bye"):
            console.print("\n  [dim]À bientôt.[/]\n")
            break

        console.print()

        try:
            callbacks = AgentCallbacks(
                on_thinking=cb.on_thinking,
                on_tool_call=cb.on_tool_call,
                on_tool_result=cb.on_tool_result,
                on_tool_denied=cb.on_tool_denied,
                on_done=cb.on_done,
                on_error=cb.on_error,
                on_collect_credential=cb.on_collect_credential,
            )
            _, history = await run(
                goal=goal,
                provider=provider,
                pilot_client=pilot_client,
                gate=gate,
                callbacks=callbacks,
                history=history if history else None,
                max_steps=max_steps,
            )
        except Exception as e:
            cb._stop_live()
            cb.on_error(str(e))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_args(args: dict[str, Any]) -> str:
    """Formate les args en '(k='v', ...)' compact pour l'affichage inline."""
    if not args:
        return ""
    # Filtre les valeurs longues (ex: contenu de fichiers)
    parts = []
    for k, v in list(args.items())[:3]:
        sv = str(v)
        if len(sv) > 40:
            sv = sv[:37] + "…"
        parts.append(f"{k}={sv!r}")
    suffix = "…" if len(args) > 3 else ""
    return " (" + ", ".join(parts) + suffix + ")"


def _has_markdown(text: str) -> bool:
    """Détecte si le texte contient du Markdown non-trivial."""
    import re
    return bool(re.search(r"[*_`#\[\]]", text))
