"""
LLMController — gerencia o ciclo de vida das LLMs (Ollama) com contagem de uso
ENTRE processos, para o shutdown gracioso.

Regra: o coletor/watcher e o poster usam o MESMO modelo (qwen2.5:3b). Cada um se
registra como "consumidor" enquanto precisa da LLM. O Ollama só é encerrado quando
NÃO há nenhum consumidor ativo — assim, ao desligar o watcher, a LLM continua viva
até o poster terminar de gerar as captions da fila.

Uso típico:
    llm = LLMController()
    llm.adquirir("watcher")     # ao começar a produzir
    ...
    llm.liberar("watcher")      # ao terminar; encerra o Ollama se ninguém mais usa
"""

import subprocess

from django.conf import settings
from django.utils import timezone

from core.models import ConsumidorLLM
from core.services import systemd


class LLMController:
    def __init__(self, unit=None, modelos=None):
        self.unit = unit or settings.OLLAMA_SYSTEMD_UNIT
        self.modelos = modelos or settings.OLLAMA_MODELOS

    # -- registro de consumidores -------------------------------------------
    def adquirir(self, consumidor):
        """Marca um consumidor como ativo e garante o Ollama ligado."""
        ConsumidorLLM.objects.update_or_create(
            nome=consumidor,
            defaults={"ativo": True, "heartbeat": timezone.now()},
        )
        self.garantir_ligado()

    def liberar(self, consumidor):
        """Marca o consumidor como ocioso; encerra o Ollama se ninguém mais usa."""
        ConsumidorLLM.objects.update_or_create(
            nome=consumidor, defaults={"ativo": False, "heartbeat": timezone.now()},
        )
        if not self.ha_consumidores_ativos():
            self.desligar()

    def heartbeat(self, consumidor):
        ConsumidorLLM.objects.filter(nome=consumidor).update(heartbeat=timezone.now())

    def ha_consumidores_ativos(self):
        return ConsumidorLLM.objects.filter(ativo=True).exists()

    # -- controle do serviço Ollama -----------------------------------------
    def esta_ligado(self):
        return systemd.esta_ativo(self.unit)

    def garantir_ligado(self):
        if not self.esta_ligado():
            systemd.iniciar(self.unit)

    def desligar(self):
        """Descarrega os modelos da memória e para o serviço Ollama.
        Primeiro tenta `ollama stop <modelo>` (libera RAM sem derrubar o serviço);
        depois para o serviço systemd, que zera o uso de recursos."""
        for modelo in self.modelos:
            subprocess.run(["ollama", "stop", modelo], check=False)
        systemd.parar(self.unit)
        return True
