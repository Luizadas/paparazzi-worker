#!/usr/bin/env python3
# start.py - Inicializador do Paparazzi Worker
#
# Menu interativo para subir as dependências e iniciar um dos dois sistemas:
#   1. Paparazzi (gera vídeos novos via IA)
#   2. Mirror    (espelha Shorts e posta no TikTok)
# Também permite atualizar o TIKTOK_SESSION_ID e checar dependências.
#
# Uso:  python3 start.py

import os
import sys
import shutil
import subprocess
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
REQ_FILE = ROOT / "requirements.txt"

# módulo importável -> pacote pip (para checar o que falta)
MODULOS = {
    "yt_dlp": "yt-dlp",
    "whisper": "openai-whisper",
    "torch": "torch",
    "numpy": "numpy",
    "PIL": "Pillow",
    "selenium": "selenium",
    "webdriver_manager": "webdriver-manager",
    "googleapiclient": "google-api-python-client",
    "isodate": "isodate",
    "dotenv": "python-dotenv",
    "requests": "requests",
    "tweepy": "tweepy",
}


# ─────────────────────────────────────────────
#  Dependências
# ─────────────────────────────────────────────

def _modulos_faltando():
    return [pip for mod, pip in MODULOS.items() if importlib.util.find_spec(mod) is None]


def subir_dependencias(forcar=False):
    """Garante que as dependências Python estão instaladas e checa ffmpeg/Ollama."""
    print("\n🔧 Verificando dependências...")
    faltando = _modulos_faltando()

    if faltando or forcar:
        alvo = "requirements.txt" if forcar else f"faltando: {', '.join(faltando)}"
        print(f"📦 Instalando dependências ({alvo})...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--user", "-r", str(REQ_FILE)],
            check=False,
        )
    else:
        print("✅ Dependências Python OK.")

    # FFmpeg (não é pacote pip)
    if shutil.which("ffmpeg"):
        print("✅ ffmpeg encontrado.")
    else:
        print("⚠️  ffmpeg NÃO encontrado. Instale com: sudo apt install ffmpeg")

    _checar_ollama()


def _checar_ollama():
    """Ollama é usado para gerar a legenda do post (IA) e o roteiro do paparazzi."""
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        if r.ok:
            modelos = [m["name"] for m in r.json().get("models", [])]
            print(f"✅ Ollama rodando. Modelos: {', '.join(modelos) or '(nenhum baixado)'}")
            return
    except Exception:
        pass
    print("⚠️  Ollama não respondeu em localhost:11434 (necessário p/ legenda por IA).")
    print("    Inicie o Ollama antes de rodar, se for usar a legenda gerada por IA.")


# ─────────────────────────────────────────────
#  .env / session_id
# ─────────────────────────────────────────────

def _set_env(chave, valor):
    linhas = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    saida, achou = [], False
    for linha in linhas:
        if linha.strip().startswith(f"{chave}="):
            saida.append(f"{chave}={valor}")
            achou = True
        else:
            saida.append(linha)
    if not achou:
        saida.append(f"{chave}={valor}")
    ENV_FILE.write_text("\n".join(saida) + "\n", encoding="utf-8")


def _get_env(chave):
    if not ENV_FILE.exists():
        return ""
    for linha in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if linha.strip().startswith(f"{chave}="):
            return linha.split("=", 1)[1].strip()
    return ""


def atualizar_session_id():
    atual = _get_env("TIKTOK_SESSION_ID")
    if atual:
        print(f"\nsession_id atual: {atual[:6]}...{atual[-4:]} ({len(atual)} caracteres)")
    else:
        print("\nNenhum session_id configurado ainda.")
    print("Como pegar: logue no TikTok pelo Chrome → F12 → Application →")
    print("Cookies → tiktok.com → copie o valor do cookie 'sessionid'.")
    novo = input("\nCole o novo TIKTOK_SESSION_ID (ou Enter para cancelar): ").strip()
    if not novo:
        print("Cancelado.")
        return
    _set_env("TIKTOK_SESSION_ID", novo)
    print(f"✅ session_id atualizado no .env ({len(novo)} caracteres).")


# ─────────────────────────────────────────────
#  Execução dos sistemas
# ─────────────────────────────────────────────

def _rodar(pasta, *args):
    destino = ROOT / pasta / "main.py"
    if not destino.exists():
        print(f"❌ Não encontrei {destino}")
        return
    print(f"\n🚀 Iniciando: {pasta} {' '.join(args)}".rstrip())
    print("=" * 45)
    try:
        subprocess.run([sys.executable, "main.py", *args], cwd=str(ROOT / pasta), check=False)
    except KeyboardInterrupt:
        print("\n⏹️  Interrompido pelo usuário.")


def iniciar_paparazzi():
    subir_dependencias()
    _rodar("sistema_paparazzi")


def iniciar_mirror():
    subir_dependencias()
    while True:
        print("""
--- MIRROR ---
1. Fluxo completo (detector → watcher → poster)
2. Sem detector (checar watch_list e postar)
3. Só postar a fila
0. Voltar
""")
        op = input("Escolha: ").strip()
        if op == "1":
            _rodar("mirror_clips"); break
        elif op == "2":
            _rodar("mirror_clips", "--sem-detector"); break
        elif op == "3":
            _rodar("mirror_clips", "--so-poster"); break
        elif op == "0":
            return
        else:
            print("Opção inválida.")


# ─────────────────────────────────────────────
#  Menu principal
# ─────────────────────────────────────────────

def menu():
    while True:
        print("""
==================================
        PAPARAZZI WORKER
==================================
1. Paparazzi  (gera vídeos novos via IA)
2. Mirror     (espelha Shorts + posta no TikTok)
3. Atualizar TikTok session_id
4. Verificar/instalar dependências
0. Sair
""")
        op = input("Escolha uma opção: ").strip()
        if op == "1":
            iniciar_paparazzi()
        elif op == "2":
            iniciar_mirror()
        elif op == "3":
            atualizar_session_id()
        elif op == "4":
            subir_dependencias(forcar=True)
        elif op == "0":
            print("Até mais! 👋")
            break
        else:
            print("Opção inválida.")


if __name__ == "__main__":
    try:
        menu()
    except KeyboardInterrupt:
        print("\nAté mais! 👋")
