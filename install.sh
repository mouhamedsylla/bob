#!/usr/bin/env sh
# bob — script d'installation
# Usage : curl -fsSL https://raw.githubusercontent.com/mouhamedsylla/bob/main/install.sh | sh

set -e

REPO="https://github.com/mouhamedsylla/bob"
MIN_PYTHON="3.12"

# ── Couleurs ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { printf "${CYAN}→${RESET}  %s\n" "$1"; }
success() { printf "${GREEN}✓${RESET}  %s\n" "$1"; }
warn()    { printf "${YELLOW}⚠${RESET}  %s\n" "$1"; }
error()   { printf "${RED}✗${RESET}  %s\n" "$1" >&2; exit 1; }

# ── Header ────────────────────────────────────────────────────────────────────
printf "\n${BOLD}  bob installer${RESET}\n"
printf "  Agent IA pour pilot — orchestre ton infra en langage naturel\n\n"

# ── Vérification prérequis ────────────────────────────────────────────────────

# pilot doit être installé
if ! command -v pilot >/dev/null 2>&1; then
  error "pilot n'est pas installé.\n  Installe-le d'abord : https://github.com/mouhamedsylla/pilot"
fi
success "pilot détecté : $(pilot version 2>/dev/null | head -1)"

# Python 3.12+
PYTHON=""
for cmd in python3.12 python3.13 python3 python; do
  if command -v "$cmd" >/dev/null 2>&1; then
    ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    if [ "$major" -ge 3 ] && [ "$minor" -ge 12 ]; then
      PYTHON="$cmd"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  error "Python ${MIN_PYTHON}+ requis.\n  Télécharge-le : https://python.org/downloads"
fi
success "Python détecté : $($PYTHON --version)"

# ── Choix de l'installeur ─────────────────────────────────────────────────────
if command -v uv >/dev/null 2>&1; then
  INSTALLER="uv"
  info "uv détecté — méthode recommandée"
elif command -v pipx >/dev/null 2>&1; then
  INSTALLER="pipx"
  info "pipx détecté"
else
  INSTALLER="pip"
  warn "ni uv ni pipx détectés — utilisation de pip (recommandé : https://docs.astral.sh/uv)"
fi

# ── Installation ──────────────────────────────────────────────────────────────
info "Installation de bob..."

case "$INSTALLER" in
  uv)
    uv tool install "git+${REPO}" || error "Installation échouée"
    ;;
  pipx)
    pipx install "git+${REPO}" || error "Installation échouée"
    ;;
  pip)
    "$PYTHON" -m pip install --user "git+${REPO}" || error "Installation échouée"
    ;;
esac

# ── Vérification ──────────────────────────────────────────────────────────────
if command -v bob >/dev/null 2>&1; then
  success "bob installé : $(bob --version 2>/dev/null || echo 'ok')"
else
  warn "bob installé mais pas dans le PATH."
  printf "  Ajoute ceci à ton ~/.bashrc ou ~/.zshrc :\n"
  case "$INSTALLER" in
    uv)   printf '    export PATH="$HOME/.local/bin:$PATH"\n' ;;
    pipx) printf '    export PATH="$HOME/.local/bin:$PATH"\n' ;;
    pip)  printf '    export PATH="$HOME/.local/bin:$PATH"\n' ;;
  esac
fi

# ── Prochaines étapes ─────────────────────────────────────────────────────────
printf "\n${BOLD}  Prochaines étapes${RESET}\n\n"
printf "  1. Configure ton LLM :\n"
printf "     ${CYAN}export ANTHROPIC_API_KEY=sk-ant-...${RESET}  # Claude (défaut)\n"
printf "     ${CYAN}export OPENAI_API_KEY=sk-...${RESET}          # GPT-4\n"
printf "     ${CYAN}# Aucune clé pour Ollama (local)${RESET}\n\n"
printf "  2. Lance l'agent depuis un projet pilot :\n"
printf "     ${CYAN}cd mon-projet && bob${RESET}\n\n"
printf "  Docs : ${REPO}#readme\n\n"
