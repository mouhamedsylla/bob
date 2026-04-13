"""
Boucle Think / Act / Observe — logique pure, sans affichage.

L'UI est injectée via AgentCallbacks : terminal, Telegram, tests — même boucle.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from pilot_agent.gates.approval import ApprovalGate, ApprovalRequest, approval_reason
from pilot_agent.llm.provider import LLMProvider, Message
from pilot_agent.mcp.client import MCPHub

# ── Callbacks — interface d'affichage ─────────────────────────────────────────

@dataclass
class AgentCallbacks:
    """
    Tous les callbacks sont optionnels.
    L'UI les implémente ; les tests peuvent les ignorer.
    """
    on_thinking:           Callable[[], None]                            = lambda: None
    on_message:            Callable[[str], None]                         = lambda m: None
    on_tool_call:          Callable[[str, dict], None]                   = lambda n, a: None
    on_tool_result:        Callable[[str, str, float], None]             = lambda n, r, ms: None
    on_tool_denied:        Callable[[str], None]                         = lambda n: None
    on_token:              Callable[[str], None]                         = lambda t: None
    on_done:               Callable[[str], None]                         = lambda r: None
    on_error:              Callable[[str], None]                         = lambda e: None
    # Virtual tool : demande une valeur à l'utilisateur inline
    on_collect_credential: Callable[[str, str, bool], Awaitable[str]]   = None  # type: ignore


SYSTEM_PROMPT = """\
Tu es pilot-agent — un DevOps senior intégré à pilot. Tu agis, tu ne commentes pas.

PRINCIPES FONDAMENTAUX
1. Action d'abord. Si tu as les outils pour avancer, avance.
2. Erreur ou retry : OBLIGATOIRE — écris UNE phrase avant de relancer quoi que ce soit.
   Format : "X a échoué (raison concrète). Je vais faire Y."
   L'utilisateur ne voit pas les résultats bruts des outils — sans ta phrase, il ne comprend rien.
3. Changement de stratégie : avant de tenter une approche différente, écris ce que tu changes et pourquoi.
4. Bloqué ? Une phrase sur le blocage + une question directe. Pas de liste, pas d'alternatives.
5. Réponse finale : 1 à 3 lignes. Jamais de "prochaines étapes", jamais de résumé de ce que tu viens de faire.
6. Warnings non-bloquants dans pilot_preflight : continue quand même sauf instruction contraire.
7. Commence par pilot_context si le projet n'est pas encore connu dans la conversation.

ENVIRONNEMENTS — RÈGLE ABSOLUE
- "local", "en local", "dev", "relance", "redémarre", "recharge" → pilot_up sur env=dev. Jamais pilot_push ni pilot_deploy.
- "prod", "production", "déploie en prod", "push" → demande confirmation explicite : "Tu veux déployer en production (pilot_push + pilot_deploy) ?" avant d'agir.
- En cas d'ambiguïté sur l'env cible, pose une question directe. Ne suppose jamais prod.

VARIABLES D'ENVIRONNEMENT
Règle absolue : ne jamais lire .env.dev (ou tout autre env) pour construire .env.prod. Zéro transfert de valeurs entre envs.

Quand .env.<env> est manquant :
  a. Appelle pilot_env_create(env=...) directement — il génère les secrets, applique les defaults,
     et documente les credentials externes avec instructions. Ne demande pas la permission.
  b. Une fois créé, lis le rapport retourné. Si external ou unknown > 0 :
     "J'ai créé .env.prod. Il reste N valeur(s) à configurer manuellement — ouvre le fichier,
      les commentaires indiquent exactement où obtenir chaque credential."
  c. Si .env.example n'existe pas : informe et demande si l'app a besoin de variables d'env.
  d. Un env file manquant n'est PAS un bloquant automatique — si l'humain dit "continue sans", on continue.

GÉNÉRATION DE FICHIERS INFRA
Pour Dockerfiles et docker-compose : utilise Context7 (resolve-library-id + get-library-docs) pour vérifier la syntaxe exacte.
Ces outils sont optionnels — si absents, continue sans.

MIGRATIONS — RÈGLE ABSOLUE
Les outils de migration (alembic, prisma, goose, django…) vivent DANS le container Docker, pas sur le VPS.
pilot_deploy exécute les migrations via `docker compose run --rm` — c'est automatique, pilot s'en charge.
N'utilise JAMAIS pilot_vps_exec pour installer ou lancer un outil de migration. Si les migrations échouent :
  - Vérifie que l'image est à jour (pilot_push).
  - Vérifie que .env.<env> contient DATABASE_URL.
  - Ne tente pas d'installer l'outil sur le VPS host.

RÉSOLUTION DE PROBLÈMES SUR LE VPS
pilot_vps_exec sert uniquement pour les outils système (nginx, curl, docker, systemd…), pas pour des dépendances applicatives.
Si un outil système est manquant (exit status 127) :
  1. Identifie le package système à installer (ex: curl → apt-get install -y curl).
  2. Appelle pilot_vps_exec avec UNE SEULE commande d'installation.
  3. Vérifie avec pilot_vps_exec que l'outil est disponible (<tool> --version).
  4. Relance l'opération qui avait échoué.
Règle absolue : une commande par appel pilot_vps_exec. Jamais de && entre commandes.
"""


@dataclass
class AgentRun:
    messages: list[Message] = field(default_factory=list)
    steps: int = 0


async def run(
    goal: str,
    provider: LLMProvider,
    pilot_client: MCPHub,
    gate: ApprovalGate,
    callbacks: AgentCallbacks | None = None,
    history: list[Message] | None = None,
    max_steps: int = 20,
) -> tuple[str, list[Message]]:
    """
    Lance la boucle agentique.

    Retourne (réponse_finale, messages_complets).
    `history` permet de continuer une conversation existante (REPL multi-tours).
    """
    cb = callbacks or AgentCallbacks()

    state = AgentRun()
    # Initialise ou continue la conversation
    if history:
        state.messages = history
        state.messages.append(Message(role="user", content=goal))
    else:
        state.messages = [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=goal),
        ]

    while state.steps < max_steps:
        state.steps += 1
        cb.on_thinking()

        response = await provider.complete(state.messages, pilot_client.tool_schemas())

        if response.is_final:
            final = response.content or ""
            cb.on_done(final)
            state.messages.append(Message(role="assistant", content=final))
            return final, state.messages

        # Texte intermédiaire de bob (raisonnement, explication d'erreur…)
        if response.content and response.content.strip():
            cb.on_message(response.content.strip())

        # Assistant message avec tool_calls
        state.messages.append(
            Message(
                role="assistant",
                content=response.content,
                tool_calls=[
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ],
            )
        )

        for tool_call in response.tool_calls:
            cb.on_tool_call(tool_call.name, tool_call.arguments)

            # ── Virtual tools — gérés par l'UI, pas par MCP ───────────────
            if pilot_client.is_virtual(tool_call.name):
                result = await _handle_virtual_tool(tool_call, cb, pilot_client)
                state.messages.append(
                    Message(role="tool", tool_call_id=tool_call.id, content=result)
                )
                continue

            # Garde-fou humain
            if pilot_client.is_destructive(tool_call.name):
                approved = await gate.request(
                    ApprovalRequest(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        reason=approval_reason(tool_call.name),
                    )
                )
                if not approved:
                    cb.on_tool_denied(tool_call.name)
                    result = "Action annulée par l'utilisateur."
                    state.messages.append(
                        Message(role="tool", tool_call_id=tool_call.id, content=result)
                    )
                    continue

            t0 = time.monotonic()
            try:
                result = await pilot_client.call(tool_call.name, tool_call.arguments)
            except RuntimeError as e:
                result = f"Erreur : {e}"
            elapsed = (time.monotonic() - t0) * 1000
            cb.on_tool_result(tool_call.name, result, elapsed)

            state.messages.append(
                Message(role="tool", tool_call_id=tool_call.id, content=result)
            )

    return "Max steps atteint.", state.messages


# ── Virtual tool handler ──────────────────────────────────────────────────────

async def _handle_virtual_tool(
    tool_call: Any,
    cb: AgentCallbacks,
    hub: MCPHub,
) -> str:
    """Dispatch les virtual tools vers les callbacks UI appropriés."""

    if tool_call.name == "collect_credential":
        key    = tool_call.arguments.get("key", "")
        prompt = tool_call.arguments.get("prompt", key)
        secret = tool_call.arguments.get("secret", True)

        if cb.on_collect_credential is None:
            # Fallback sans UI — ne devrait pas arriver en prod
            return f"collect_credential: aucun callback UI enregistré pour {key!r}"

        try:
            value = await cb.on_collect_credential(key, prompt, secret)
        except Exception as e:
            return f"Collecte annulée : {e}"

        if not value:
            return f"Collecte annulée — aucune valeur fournie pour {key!r}"

        # Stocke dans le process pilot (MCP server) + .env.local
        try:
            result = await hub.call("pilot_credential_set", {"key": key, "value": value})
            cb.on_tool_result("pilot_credential_set", result, 0)
            return f"✓ {key} configuré et persisté dans .env.local"
        except Exception as e:
            return f"✓ {key} collecté mais non persisté : {e}"

    return f"Virtual tool {tool_call.name!r} non géré."
