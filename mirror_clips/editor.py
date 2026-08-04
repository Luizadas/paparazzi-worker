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


def modo_daemon(deve_parar=lambda: False):
    """Fica vivo consumindo a fila de edição. Parada graciosa: `deve_parar` é
    checado ENTRE vídeos — o vídeo em edição termina normalmente (não é abortado)."""
    import coletor   # importa aqui (evita carregar modelos se o serviço não editar nada)

    print("✂️  Editor em modo DAEMON — processando a fila de edição.")
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
