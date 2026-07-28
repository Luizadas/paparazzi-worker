# main.py - Orquestrador do fluxo Mirror-Clips
#
# Fluxo completo:
#   1. detector.py  -> encontra Shorts (<=70s) e popula a watch_list
#   2. watcher.py   -> checa views; Shorts com >=100k disparam o coletor
#                      (coletor baixa, espelha, legenda e enfileira para postagem)
#   3. poster.py    -> posta no TikTok os vídeos que estão na fila
#
# Uso:
#   python main.py                 # roda o fluxo completo (detector -> watcher -> poster)
#   python main.py --sem-detector  # pula a detecção (útil para checar/postar a fila atual)
#   python main.py --so-poster     # só processa a fila de postagem

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent


def executar_script(nome_script, *args):
    """Executa um script do módulo (no diretório do mirror-clips) e reporta o resultado."""
    print(f"\n{'='*55}")
    print(f" ▶ EXECUTANDO: {nome_script} {' '.join(args)}".rstrip())
    print(f"{'='*55}")

    resultado = subprocess.run(
        [sys.executable, str(BASE_DIR / nome_script), *args],
        cwd=str(BASE_DIR),
    )

    if resultado.returncode != 0:
        print(f"\n⚠️  {nome_script} terminou com código {resultado.returncode}.")
        return False

    print(f"\n✅ {nome_script} concluído.")
    return True


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--so-poster" in args:
        executar_script("poster.py", "--queue")
        sys.exit(0)

    # 1. Detecção de Shorts novos (a menos que pedido para pular)
    if "--sem-detector" not in args:
        executar_script("detector.py")

    # 2. Watcher: verifica performance e dispara o coletor para os virais (>=100k).
    #    O coletor roda de forma BLOQUEANTE, então ao terminar o watcher a fila
    #    de postagem já está populada com os vídeos processados.
    executar_script("watcher.py")

    # 3. Postagem: envia para o TikTok tudo que ficou pendente na fila.
    executar_script("poster.py", "--queue")

    print(f"\n{'='*55}")
    print(" 🎬 Fluxo Mirror-Clips concluído!")
    print(f"{'='*55}")
