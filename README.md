# pilot-agent

Agent IA pour [pilot](https://github.com/mouhamedsylla/pilot) — orchestre ton infrastructure en langage naturel depuis le terminal.

```
❯  déploie l'app en prod et vérifie que tout tourne

  ⚙  pilot_context
  ✓  142ms
  ⚙  pilot_preflight  env='prod'
  ✓  891ms
  ⚙  pilot_deploy  env='prod', target='vps-prod'
  ✓  12.3s

╭──────────────────────────────────────────────────────────╮
│ Déploiement terminé. L'app tourne sur **vps-prod**.      │
│ • Image : ghcr.io/toi/mon-app:a3f2c1d                   │
│ • Port 443 exposé, health check OK                       │
╰──────────────────────────────────────────────────────────╯
```

---

## Prérequis

| Dépendance | Version minimale | Vérifier |
|---|---|---|
| [pilot](https://github.com/mouhamedsylla/pilot) | toute | `pilot version` |
| Python | 3.12 | `python3 --version` |

---

## Installation

### Méthode recommandée — uv (rapide, isolé)

```sh
curl -fsSL https://raw.githubusercontent.com/mouhamedsylla/pilot-agent/main/install.sh | sh
```

Le script détecte automatiquement `uv`, `pipx` ou `pip` et installe pilot-agent depuis GitHub.

### Manuellement

```sh
# uv (recommandé)
uv tool install "git+https://github.com/mouhamedsylla/pilot-agent"

# pipx
pipx install "git+https://github.com/mouhamedsylla/pilot-agent"

# pip
pip install --user "git+https://github.com/mouhamedsylla/pilot-agent"
```

### Vérifier l'installation

```sh
pilot-agent --version
```

Si la commande n'est pas trouvée, ajoute `~/.local/bin` à ton PATH :

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc  # ou ~/.bashrc
source ~/.zshrc
```

---

## Configuration du LLM

pilot-agent supporte plusieurs LLM via [litellm](https://github.com/BerriAI/litellm). Configure la clé du provider que tu utilises :

```sh
# Claude (défaut — recommandé)
export ANTHROPIC_API_KEY="sk-ant-..."

# OpenAI
export OPENAI_API_KEY="sk-..."

# Google Gemini
export GEMINI_API_KEY="..."

# Mistral
export MISTRAL_API_KEY="..."

# DeepSeek (API cloud — https://platform.deepseek.com)
export DEEPSEEK_API_KEY="sk-..."

# Ollama (local, aucune clé requise)
# Assure-toi qu'Ollama tourne : ollama serve
```

Mets la variable dans ton `~/.zshrc` / `~/.bashrc` pour ne pas avoir à la re-saisir.

---

## Démarrage rapide

```sh
# 1. Va dans un projet pilot
cd mon-projet

# 2. Lance le REPL interactif
pilot-agent

# 3. Décris ce que tu veux faire
❯  quel est l'état de mes services ?
❯  lance l'environnement de dev
❯  déploie en prod
❯  montre-moi les logs du service api
```

### One-shot (sans REPL)

```sh
# Pose une question directement
pilot-agent "quel est l'état de mes services ?"

# Avec un LLM différent
pilot-agent "déploie en prod" --llm gpt4

# Depuis un autre répertoire
pilot-agent "status" --dir /chemin/vers/mon-projet

# Auto-approuver les actions destructives (CI/CD)
pilot-agent "déploie en prod" --yes
```

---

## Référence des options

```
pilot-agent [OBJECTIF] [OPTIONS]
```

| Option | Alias | Description | Défaut |
|---|---|---|---|
| `--llm` | `-l` | Modèle à utiliser (voir tableau ci-dessous) | `claude` |
| `--dir` | `-d` | Répertoire du projet pilot | répertoire courant |
| `--yes` | `-y` | Auto-approuve les actions destructives | `false` |
| `--max-steps` | | Nombre max d'itérations de la boucle agentique | `20` |

### Raccourcis de modèles (LLMs cloud)

| Raccourci | Modèle | Clé requise |
|---|---|---|
| `claude` | claude-3-5-sonnet-20241022 | `ANTHROPIC_API_KEY` |
| `claude-h` | claude-3-haiku-20240307 | `ANTHROPIC_API_KEY` |
| `gpt4` | gpt-4o | `OPENAI_API_KEY` |
| `gpt4m` | gpt-4o-mini | `OPENAI_API_KEY` |
| `gemini` | gemini-1.5-pro | `GEMINI_API_KEY` |
| `mistral` | mistral-large-latest | `MISTRAL_API_KEY` |
| `deepseek` | deepseek-chat | `DEEPSEEK_API_KEY` |
| `deepseek-r` | deepseek-reasoner (R1) | `DEEPSEEK_API_KEY` |

### Ollama (local)

Ollama n'a pas de raccourci fixe — chaque installation a ses propres modèles. Précise le modèle que tu as installé :

```sh
# Liste tes modèles disponibles
ollama list

# Utilise celui que tu as
pilot-agent --llm ollama/gemma3
pilot-agent --llm ollama/llama3.2
pilot-agent --llm ollama/mistral
```

Si tu tapes `--llm ollama` sans préciser le modèle, pilot-agent liste automatiquement les modèles disponibles sur ton Ollama.

Tu peux aussi passer n'importe quel identifiant litellm directement :

```sh
pilot-agent --llm "anthropic/claude-opus-4"
pilot-agent --llm "ollama/gemma3:27b"
```

---

## Commandes disponibles dans le REPL

| Commande | Action |
|---|---|
| `exit` / `quit` / `q` | Quitter |
| `Ctrl+D` | Quitter |
| `Ctrl+C` | Annuler la requête en cours |
| `↑` / `↓` | Naviguer dans l'historique |

---

## Exemples concrets

### Gestion de l'infra locale

```
❯  lance l'environnement de dev
❯  arrête tous les services
❯  redis est-il démarré ?
❯  montre-moi les logs de postgres depuis 10 minutes
```

### Déploiement

```
❯  déploie la branche main en production
❯  fais un rollback sur prod
❯  quel est l'état du déploiement ?
❯  vérifie les prérequis avant de déployer
```

### Configuration et secrets

```
❯  quelles variables d'environnement manquent en prod ?
❯  quelle image est configurée dans le registry ?
❯  injecte les secrets de prod dans le service api
```

### Debug

```
❯  pourquoi mon service api ne démarre pas ?
❯  compare la config dev et prod
❯  qu'est-ce qui a changé dans pilot.yaml ?
```

---

## Actions destructives — confirmation humaine

Certaines actions demandent une confirmation explicite avant d'être exécutées :

```
╭─────────────────────────────────────────────────╮
│ ⚠  Action requiert une confirmation             │
│                                                  │
│   Outil  : pilot_deploy                          │
│   Args   : env='prod', target='vps-prod'         │
│                                                  │
│   Déploiement irréversible en production         │
╰─────────────────────────────────────────────────╯
  Confirmer ? [o/N]
```

Actions concernées : `deploy`, `rollback`, `down`, `push`, `secrets inject`.

Pour bypasser en CI/CD : `pilot-agent "..." --yes`

---

## Architecture

```
pilot-agent
├── cli.py           — point d'entrée, mode REPL vs one-shot
├── loop/
│   └── agent.py     — boucle Think / Act / Observe
├── llm/
│   └── provider.py  — interface LLMProvider + LiteLLMProvider
├── mcp/
│   └── client.py    — connexion au serveur MCP de pilot
├── gates/
│   └── approval.py  — garde-fou humain pour les actions destructives
└── ui/
    └── repl.py      — interface terminal (spinner, tool calls, Markdown)
```

L'agent se connecte au serveur MCP embarqué dans `pilot` (`pilot mcp serve`) et dispose de tous les outils pilot : `pilot_context`, `pilot_up`, `pilot_down`, `pilot_deploy`, `pilot_rollback`, `pilot_push`, `pilot_logs`, `pilot_status`, `pilot_secrets_inject`, etc.

---

## Sécurité

- Les versions **1.82.7** et **1.82.8** de litellm contenaient du code malveillant (supply chain attack sur PyPI). pilot-agent est épinglé sur `>=1.83.0,<2.0.0`.
- Utilise `uv` avec lockfile pour vérifier les hashes SHA-256 de toutes les dépendances en CI.

---

## Développement

```sh
git clone https://github.com/mouhamedsylla/pilot-agent
cd pilot-agent

# Installation en mode éditable avec uv
uv sync
uv run pilot-agent

# Tests
uv run pytest
```

---

## Licence

MIT — voir [LICENSE](LICENSE)



  ╭─╮╭─╮   Changes   +0 -0
  ╰─╯╰─╯   Requests  0 Premium (2m 20s)
  █ ▘▝ █   Resume    copilot --resume
   ▔▔▔▔
