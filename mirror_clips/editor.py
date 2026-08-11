# editor.py - Serviço de EDIÇÃO (Mirror-Clips)
#
# Separado do watcher: o watcher só DETECTA e registra candidatos no banco;
# o EDITOR processa (baixa + espelha + blur + legenda + enfileira o post) os
# vídeos escolhidos. Espelha o poster:
#   - AutoEditor DESMARCADO: só edita os que você selecionou (status fila_edicao).
#   - AutoEditor MARCADO: puxa também os candidatos (status detectado) automaticamente.
#
# Os modelos pesados (faster-whisper medium + EasyOCR) ficam carregados no processo
# e são reusados entre os vídeos. Segura a LLM ('editor') só enquanto há trabalho.

import sys
import time
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comum import db_bridge


def _limpar_restos(idade_min=30):
    """
    Apaga os arquivos intermediários que uma edição interrompida deixa para trás
    (.part do yt-dlp, _audio.wav, .ass e o mp4 baixado antes de virar _final).
    Sem isso cada queda deixava dezenas de MB órfãos na pasta.

    Só toca no que tem mais de `idade_min` minutos: assim nunca apaga o material
    de uma edição em andamento (a partida do serviço não pode atropelar outra
    instância que ainda esteja fechando o vídeo).
    """
    import os, time
    pasta = os.getenv("MIRROR_OUTPUT_DIR", "/mnt/paparazzi/mirror_clips")
    corte = time.time() - idade_min * 60
    n = tot = 0
    try:
        for nome in os.listdir(pasta):
            if nome.endswith("_final.mp4"):
                continue                       # o produto final nunca é resto
            if not nome.endswith((".part", "_audio.wav", ".ass", ".mp4", ".jpg", ".png")):
                continue
            caminho = os.path.join(pasta, nome)
            try:
                if os.path.getmtime(caminho) < corte:
                    tot += os.path.getsize(caminho)
                    os.remove(caminho)
                    n += 1
            except OSError:
                pass
    except OSError as e:
        print(f"⚠️  limpeza de restos ignorada ({e}).")
        return
    if n:
        print(f"🧹 {n} arquivo(s) intermediário(s) de edições interrompidas "
              f"removidos ({tot/1e6:.0f} MB).")


def modo_daemon(deve_parar=lambda: False):
    """Fica vivo consumindo a fila de edição. Parada graciosa: `deve_parar` é
    checado ENTRE vídeos — o vídeo em edição termina normalmente (não é abortado)."""
    import coletor   # importa aqui (evita carregar modelos se o serviço não editar nada)

    print("✂️  Editor em modo DAEMON — processando a fila de edição.")
    # Partida = não há nada em andamento por definição. Então tudo que ficou em
    # 'processando' é resto de uma parada não-graciosa (queda de energia, kill -9)
    # e precisa voltar para a fila — senão o vídeo fica preso nesse estado para
    # sempre e some sem virar arquivo.
    db_bridge.retomar_travados("editor")
    _limpar_restos()
    segurando_llm = False

    def _adquirir():
        nonlocal segurando_llm
        if not segurando_llm:
            db_bridge.llm_adquirir("editor")   # a edição usa a LLM (legenda + meme)
            segurando_llm = True

    def _liberar():
        nonlocal segurando_llm
        if segurando_llm:
            db_bridge.llm_liberar("editor")
            segurando_llm = False

    try:
        while not deve_parar():
            auto = db_bridge.get_estado("autoeditor", "0") == "1"
            item = db_bridge.reivindicar_proxima_edicao(auto=auto)
            if item:
                _adquirir()
                print(f"✂️  Editando {item['video_id']} …")
                try:
                    coletor.processar_url(item["url"])   # roda até o fim (não abortável)
                except Exception as e:
                    print(f"❌ Erro ao editar {item['video_id']}: {e}")
                    db_bridge.marcar_edicao_falhou(item["video_id"])
                if deve_parar():
                    break
            else:
                _liberar()               # fila ociosa → solta a LLM
                for _ in range(3):
                    if deve_parar():
                        break
                    time.sleep(1)
    finally:
        _liberar()
        print("👋 Editor daemon encerrado.")


if __name__ == "__main__":
    modo_daemon()
