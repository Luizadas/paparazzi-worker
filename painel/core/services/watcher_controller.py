"""
WatcherController — liga/desliga o watcher (produtor de vídeos) via systemd,
com SHUTDOWN GRACIOSO.

Ao DESLIGAR:
  1. Marca 'parar_solicitado' e para o serviço do watcher — o systemd envia SIGTERM
     e AGUARDA (TimeoutStopSec) o watcher terminar o vídeo que está processando.
  2. O watcher, ao sair, libera seu consumo de LLM ("watcher").
  3. Se o poster já tiver esvaziado a fila (nenhum consumidor de LLM ativo), o
     LLMController encerra o Ollama. Caso contrário, a LLM segue viva até o poster
     terminar de gerar as captions da fila — só então ela é encerrada.

Não mata processo no meio: quem garante isso é o SIGTERM + espera do systemd,
combinado ao loop do watcher que só checa a parada ENTRE vídeos.
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

    # -- estado -------------------------------------------------------------
    def _set(self, chave, valor):
        EstadoSistema.objects.update_or_create(chave=chave, defaults={"valor": str(valor)})

    def _get(self, chave, default=""):
        obj = EstadoSistema.objects.filter(chave=chave).first()
        return obj.valor if obj else default

    def esta_ligado(self):
        return systemd.esta_ativo(self.unit)

    def parada_solicitada(self):
        return self._get(self.CHAVE_PARAR, "0") == "1"

    def status(self):
        return {
            "ligado": self.esta_ligado(),
            "parar_solicitado": self.parada_solicitada(),
            "ollama_ligado": self.llm.esta_ligado(),
            "consumidores_llm": self.llm.ha_consumidores_ativos(),
        }

    # -- ações --------------------------------------------------------------
    def ligar(self):
        """Sobe o watcher (que passa a produzir vídeos) e garante o Ollama ligado."""
        self._set(self.CHAVE_PARAR, "0")
        self.llm.garantir_ligado()
        ok = systemd.iniciar(self.unit)
        self._set(self.CHAVE_LIGADO, "1" if ok else "0")
        return ok

    def desligar(self):
        """Desliga o watcher de forma graciosa (espera o vídeo atual terminar) e
        encerra a LLM se ninguém mais a estiver usando."""
        self._set(self.CHAVE_PARAR, "1")
        # systemctl stop bloqueia até o watcher sair (respeitando TimeoutStopSec),
        # dando tempo do vídeo em processamento concluir.
        ok = systemd.parar(self.unit)
        self._set(self.CHAVE_LIGADO, "0")
        # Segurança: garante que o watcher não fique contando como consumidor.
        self.llm.liberar("watcher")
        return ok
