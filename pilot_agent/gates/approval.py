"""
Garde-fous humain — approuvation requise avant les actions destructives.

Ce module est le seul endroit où on décide ce qui est "dangereux".
Il est indépendant du LLM et du transport (CLI, Telegram, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class ApprovalRequest:
    tool_name: str
    arguments: dict[str, Any]
    reason: str  # phrase courte expliquant pourquoi c'est destructif


@runtime_checkable
class ApprovalGate(Protocol):
    """
    Interface du garde-fou.
    Implémentations : TerminalGate (CLI), TelegramGate (bot), AutoApprove (CI/tests).
    """
    async def request(self, req: ApprovalRequest) -> bool: ...


# ── Terminal (CLI interactif) ─────────────────────────────────────────────────

class TerminalGate:
    """Demande confirmation dans le terminal. Bloquant."""

    async def request(self, req: ApprovalRequest) -> bool:
        from rich.console import Console
        from rich.panel import Panel
        from rich import box

        console = Console()
        args_str = ", ".join(f"{k}={v!r}" for k, v in req.arguments.items())
        console.print(
            Panel(
                f"[bold yellow]⚠  Action requiert une confirmation[/]\n\n"
                f"  Outil : [bold]{req.tool_name}[/]\n"
                f"  Args  : {args_str or '(aucun)'}\n\n"
                f"  [dim]{req.reason}[/]",
                box=box.ROUNDED,
                border_style="yellow",
            )
        )
        answer = input("  Confirmer ? [o/N] : ").strip().lower()
        return answer in ("o", "oui", "y", "yes")


# ── Auto-approve (tests / CI) ─────────────────────────────────────────────────

class AutoApproveGate:
    """Approuve tout automatiquement. À utiliser uniquement en tests."""

    async def request(self, req: ApprovalRequest) -> bool:
        return True


# ── Raisons par outil ─────────────────────────────────────────────────────────

_REASONS: dict[str, str] = {
    "pilot_deploy":         "Déploie l'application en production. Irréversible sans rollback.",
    "pilot_rollback":       "Revient à une version précédente. Peut casser les migrations.",
    "pilot_down":           "Arrête les services. Interruption de service.",
    "pilot_push":           "Pousse une nouvelle image Docker sur le registry.",
    "pilot_secrets_inject": "Modifie des secrets d'environnement.",
}


def approval_reason(tool_name: str) -> str:
    return _REASONS.get(tool_name, "Action potentiellement destructive.")
