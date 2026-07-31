"""
WatcherController — liga/desliga o watcher (produtor de vídeos) via systemd,
com SHUTDOWN GRACIOSO.

Ao DESLIGAR:
  1. Marca 'parar_solicitado' e para o serviço do watcher com --no-block: o systemd
     envia SIGTERM e AGUARDA (TimeoutStopSec) o watcher terminar o vídeo que está
     processando — mas o painel retorna na hora (drenagem em segundo plano).
  2. O watcher, ao sair, libera seu consumo de LLM ("watcher"); se ninguém mais usa
     (poster ocioso), o modelo é descarregado da memória (RAM liberada).

Não mata processo no meio: o watcher só checa a parada ENTRE vídeos.
"""

from django.conf import settings

from core.models import EstadoSistema
from core.services import systemd
from core.services.llm_controller import LLMController


class WatcherController:
    CHAVE_LIGADO = "watcher_ligado"
    CHAVE_PARAR = "parar_solicitado"

    def __init__(self, unit=None, llm=None):
        self.unit = unit or settings.WATCHER_SYSTEMD_UNIT
        self.llm = llm or LLMController()

    def _set(self, chave, valor):
        EstadoSistema.objects.update_or_create(chave=chave, defaults={"valor": str(valor)})

    def _get(self, chave, default=""):
        obj = EstadoSistema.objects.filter(chave=chave).first()
        return obj.valor if obj else default

    def esta_ligado(self):
        return systemd.esta_ativo(self.unit)

    def estado(self):
        return systemd.estado(self.unit)

    def parada_solicitada(self):
        return self._get(self.CHAVE_PARAR, "0") == "1"

    def status(self):
        est = self.estado()
        return {
            "ligado": est == "active",
            "estado": est,                       # active/deactivating/inactive...
            "desligando": est == "deactivating" or (
                self.parada_solicitada() and est != "inactive"),
            "ollama_ligado": self.llm.esta_ligado(),
            "consumidores_llm": self.llm.ha_consumidores_ativos(),
        }

    def ligar(self):
        """Sobe o watcher (que passa a produzir vídeos) e garante o Ollama ligado."""
        self._set(self.CHAVE_PARAR, "0")
        self.llm.garantir_ligado()
        ok = systemd.iniciar(self.unit)
        self._set(self.CHAVE_LIGADO, "1" if ok else "0")
        return ok

    def desligar(self):
        """Pede parada graciosa (não bloqueia). O watcher termina o vídeo atual e,
        ao sair, libera a LLM. A drenagem acontece em segundo plano."""
        self._set(self.CHAVE_PARAR, "1")
        ok = systemd.parar(self.unit, no_block=True)
        return ok
