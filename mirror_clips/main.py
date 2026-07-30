# main.py - Orquestrador do fluxo Mirror-Clips (PIPELINE CONCORRENTE)
#
# Fluxo:
#   1. detector.py  -> encontra Shorts (<=70s) e popula a watch_list
#   2. watcher.py   -> checa views; virais (>=30k) disparam o coletor, que
#                      baixa, edita (áudio/legendas) e ENFILEIRA cada vídeo pronto
#   3. poster.py --watch -> roda EM PARALELO, postando cada vídeo assim que fica
#                      pronto. Assim, enquanto um vídeo é postado (lento), o
#                      próximo já está sendo processado -> menos tempo total.
#
# Uso:
#   python main.py                 # fluxo completo concorrente (detector+watcher || poster)
#   python main.py --sem-detector  # pula a detecção
#   python main.py --so-poster     # só posta a fila atual (serial)

import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
STOP_FLAG = BASE_DIR / ".processamento_concluido"
MODO_POST = "--selenium"   # modo de postagem usado pelo pipeline


def executar_script(nome_script, *args):
    """Executa um script do módulo (bloqueante) e reporta o resultado."""
    print(f"\n{'='*55}")
    print(f" ▶ EXECUTANDO: {nome_script} {' '.join(args)}".rstrip())
    print(f"{'='*55}")
    resultado = subprocess.run(
        [sys.executable, str(BASE_DIR / nome_script), *args], cwd=str(BASE_DIR),
    )
    if resultado.returncode != 0:
        print(f"\n⚠️  {nome_script} terminou com código {resultado.returncode}.")
        return False
    print(f"\n✅ {nome_script} concluído.")
    return True


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--so-poster" in args:
        executar_script("poster.py", MODO_POST, "--queue")
        sys.exit(0)

    # Limpa o sinal de conclusão de execuções anteriores
    if STOP_FLAG.exists():
        STOP_FLAG.unlink()

    # 1. Sobe o POSTADOR concorrente (fica aguardando a fila encher).
    print("🚀 Subindo o postador concorrente (poster --watch)...")
    poster = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "poster.py"), MODO_POST, "--watch"],
        cwd=str(BASE_DIR),
    )

    try:
        # 2. PROCESSAMENTO (produz vídeos na fila enquanto o poster os consome).
        if "--sem-detector" not in args:
            executar_script("detector.py")
        # watcher dispara o coletor por viral: baixa + edita + enfileira cada um.
        executar_script("watcher.py")
    finally:
        # 3. Sinaliza fim do processamento; o poster drena o restante da fila e sai.
        STOP_FLAG.touch()
        print("\n⏳ Processamento concluído. Aguardando o postador terminar a fila...")
        poster.wait()
        if STOP_FLAG.exists():
            STOP_FLAG.unlink()

    print(f"\n{'='*55}")
    print(" 🎬 Fluxo Mirror-Clips concluído!")
    print(f"{'='*55}")
