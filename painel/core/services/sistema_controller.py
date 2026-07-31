"""
SistemaController — o "botão geral". Liga/desliga watcher E poster de uma vez,
respeitando as MESMAS regras de parada graciosa de cada um.

DESLIGAR TUDO:
  - pede parada graciosa do watcher (termina o vídeo atual, para de produzir) e
  - pede parada graciosa do poster (termina o post atual, drena o que puder).
  Cada processo, ao sair, libera sua LLM; quando ninguém mais usa, o modelo é
  descarregado da memória (RAM liberada). Tudo em segundo plano — não trava.

LIGAR TUDO:
  - sobe o poster (para postar a fila) e o watcher (para produzir).
"""

from core.services.watcher_controller import WatcherController
from core.services.poster_controller import PosterController
from core.services.llm_controller import LLMController


class SistemaController:
    def __init__(self):
        self.watcher = WatcherController()
        self.poster = PosterController()
        self.llm = LLMController()

    def status(self):
        w = self.watcher.status()
        p = self.poster.status()
        return {
            "watcher": w,
            "poster": p,
            "ollama_ligado": self.llm.esta_ligado(),
            "consumidores_llm": self.llm.ha_consumidores_ativos(),
            "algo_ligado": w["ligado"] or p["ligado"],
            "algo_desligando": w["desligando"] or p["desligando"],
        }

    def ligar_tudo(self):
        return {"poster": self.poster.ligar(), "watcher": self.watcher.ligar()}

    def desligar_tudo(self):
        # Ambos graciosos e não-bloqueantes; a ordem não importa porque cada um
        # termina seu item em andamento antes de sair.
        return {"watcher": self.watcher.desligar(), "poster": self.poster.desligar()}
