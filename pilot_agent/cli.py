"""
Entry point CLI.

  pilot-agent                        → REPL interactif (mode défaut)
  pilot-agent "objectif"             → one-shot, répond et quitte
  pilot-agent --llm ollama "status"  → one-shot avec Ollama
"""
from __future__ import annotations

import asyncio
import os
from typing import Annotated, Optional

import typer
from rich.console import Console

app = typer.Typer(
    name="pilot-agent",
    help="Agent IA pour pilot — orchestre l'infrastructure en langage naturel.",
    add_completion=False,
    invoke_without_command=True,
)
console = Console()

# Raccourcis pour les LLMs cloud dont le nom de modèle est stable.
# Pour Ollama, chaque installation est différente — pas de raccourci hardcodé :
#   --llm ollama/deepseek-r1    --llm ollama/gemma3    --llm ollama/llama3.2
MODELS = {
    "claude":    "anthropic/claude-3-5-sonnet-20241022",
    "claude-h":  "anthropic/claude-3-haiku-20240307",
    "gpt4":      "openai/gpt-4o",
    "gpt4m":     "openai/gpt-4o-mini",
    "gemini":    "gemini/gemini-1.5-pro",
    "mistral":   "mistral/mistral-large-latest",
    "deepseek":  "deepseek/deepseek-chat",
    "deepseek-r": "deepseek/deepseek-reasoner",
}

_LLM_OPT = Annotated[
    str,
    typer.Option(
        "--llm", "-l",
        help=(
            "Modèle LLM.\n\n"
            "Raccourcis : claude | claude-h | gpt4 | gpt4m | gemini | mistral\n\n"
            "Ollama (local) : ollama/<modèle>  ex: ollama/gemma3, ollama/llama3.2, ollama/mistral\n\n"
            "Identifiant litellm complet : anthropic/claude-opus-4, openai/gpt-4-turbo, …"
        ),
    ),
]
_DIR_OPT = Annotated[
    Optional[str],
    typer.Option("--dir", "-d", help="Répertoire du projet pilot (défaut: cwd)"),
]
_YES_OPT = Annotated[bool, typer.Option("--yes", "-y", help="Auto-approuve les actions destructives")]
_STEPS_OPT = Annotated[int, typer.Option("--max-steps", help="Nombre max d'itérations")]
_NO_C7_OPT = Annotated[bool, typer.Option("--no-context7", help="Désactive Context7 (doc en ligne)")]


@app.callback()
def main(
    ctx: typer.Context,
    goal:        Annotated[Optional[str], typer.Argument(help="Objectif (optionnel — REPL si absent)")] = None,
    llm:         _LLM_OPT   = "claude",
    dir:         _DIR_OPT   = None,
    yes:         _YES_OPT   = False,
    max_steps:   _STEPS_OPT = 20,
    no_context7: _NO_C7_OPT = False,
) -> None:
    """
    Lance pilot-agent.

    Sans argument → REPL interactif multi-tours.
    Avec argument → réponse one-shot et quitte.

    \b
    Exemples :
      pilot-agent
      pilot-agent "quel est l'état de mes services ?"
      pilot-agent "déploie en prod" --llm claude
      pilot-agent "génère les fichiers d'infra" --llm ollama/gemma3
      pilot-agent "status" --no-context7
    """
    if ctx.invoked_subcommand is not None:
        return

    if dir:
        os.chdir(dir)

    model_id = MODELS.get(llm, llm)

    # Ollama sans nom de modèle — guide l'utilisateur
    if model_id == "ollama":
        _ollama_hint()
        raise typer.Exit(1)

    asyncio.run(
        _start(
            goal=goal,
            model_id=model_id,
            max_steps=max_steps,
            auto_approve=yes,
            with_context7=not no_context7,
        )
    )


async def _start(
    goal: str | None,
    model_id: str,
    max_steps: int,
    auto_approve: bool,
    with_context7: bool = True,
) -> None:
    from pilot_agent.gates.approval import AutoApproveGate, TerminalGate
    from pilot_agent.llm.provider import make_provider
    from pilot_agent.loop.agent import AgentCallbacks, run as agent_run
    from pilot_agent.mcp.client import MCPHub
    from pilot_agent.ui.repl import ReplCallbacks, ReplApprovalGate, start_repl

    provider = make_provider(model_id)

    try:
        async with MCPHub.connect(with_context7=with_context7) as pilot_client:

            if goal:
                # ── Mode one-shot ──────────────────────────────────────────
                gate = AutoApproveGate() if auto_approve else ReplApprovalGate()
                cb = ReplCallbacks()
                callbacks = AgentCallbacks(
                    on_thinking=cb.on_thinking,
                    on_tool_call=cb.on_tool_call,
                    on_tool_result=cb.on_tool_result,
                    on_tool_denied=cb.on_tool_denied,
                    on_done=cb.on_done,
                    on_error=cb.on_error,
                    on_collect_credential=cb.on_collect_credential,
                )
                await agent_run(
                    goal=goal,
                    provider=provider,
                    pilot_client=pilot_client,
                    gate=gate,
                    callbacks=callbacks,
                    max_steps=max_steps,
                )
            else:
                # ── Mode REPL ──────────────────────────────────────────────
                # Récupère le nom du projet depuis pilot.yaml si disponible
                project_name, active_env = _read_pilot_context()
                await start_repl(
                    provider=provider,
                    pilot_client=pilot_client,
                    project_name=project_name,
                    active_env=active_env,
                    max_steps=max_steps,
                )

    except FileNotFoundError:
        console.print(
            "\n  [red]✗[/] [bold]`pilot` introuvable dans le PATH.[/]\n"
            "  Installe pilot et assure-toi qu'il est dans ton PATH.\n"
        )
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n  [dim]Interrompu.[/]\n")


def _ollama_hint() -> None:
    """Affiche un message d'aide quand --llm ollama est passé sans nom de modèle."""
    import subprocess
    console.print("\n  [yellow]⚠[/]  [bold]Ollama : précise le modèle à utiliser.[/]\n")
    console.print("  Syntaxe :  [cyan]pilot-agent --llm ollama/<modèle>[/]\n")

    # Tente de lister les modèles installés localement
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            models = [l.split()[0] for l in lines[1:] if l.strip()]  # skip header
            if models:
                console.print("  Modèles disponibles sur ton Ollama :\n")
                for m in models:
                    name = m.split(":")[0]  # strip :latest tag
                    console.print(f"    [cyan]pilot-agent --llm ollama/{name}[/]")
                console.print()
                return
    except Exception:
        pass

    console.print("  Exemples :")
    console.print("    [cyan]pilot-agent --llm ollama/gemma3[/]")
    console.print("    [cyan]pilot-agent --llm ollama/llama3.2[/]")
    console.print("    [cyan]pilot-agent --llm ollama/mistral[/]")
    console.print()
    console.print("  Pour voir tes modèles :  [dim]ollama list[/]\n")


def _read_pilot_context() -> tuple[str, str]:
    """Lit le nom du projet et l'env actif depuis pilot.yaml / .pilot-current-env."""
    project_name = os.path.basename(os.getcwd())
    active_env = "dev"

    try:
        env_file = os.path.join(os.getcwd(), ".pilot-current-env")
        if os.path.exists(env_file):
            active_env = open(env_file).read().strip() or "dev"
    except OSError:
        pass

    try:
        import re
        yaml_file = os.path.join(os.getcwd(), "pilot.yaml")
        if os.path.exists(yaml_file):
            content = open(yaml_file).read()
            m = re.search(r"^\s+name:\s+(.+)$", content, re.MULTILINE)
            if m:
                project_name = m.group(1).strip()
    except OSError:
        pass

    return project_name, active_env


if __name__ == "__main__":
    app()
