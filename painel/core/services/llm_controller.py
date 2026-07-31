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
    def adquirir(self, consumidor, garantir=True):
        """Marca um consumidor como ativo. Se garantir=True, também sobe o Ollama.
        O poster usa garantir=False: ele publica captions já prontas e não precisa
        acender o modelo só pra postar backlog — mas conta como consumidor para o
        Ollama não ser desligado enquanto o poster ainda tem posts a fazer."""
        ConsumidorLLM.objects.update_or_create(
            nome=consumidor,
            defaults={"ativo": True, "heartbeat": timezone.now()},
        )
        if garantir:
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

    # -- controle do Ollama --------------------------------------------------
    # "Ligado" aqui = há MODELO carregado em memória (é o que consome RAM). Parar
    # o serviço inteiro é frágil (o `systemctl stop ollama` chega a dar timeout no
    # DBus). Então liberamos RAM descarregando o modelo (`ollama stop`), mantendo o
    # servidor de pé — o modelo recarrega sob demanda na próxima chamada.
    def modelos_carregados(self):
        try:
            r = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10)
            linhas = [l for l in r.stdout.strip().splitlines()[1:] if l.strip()]
            return [l.split()[0] for l in linhas]
        except Exception:
            return []

    def esta_ligado(self):
        """True se algum modelo está carregado em memória (consumindo RAM)."""
        return bool(self.modelos_carregados())

    def servico_ativo(self):
        return systemd.esta_ativo(self.unit)

    def garantir_ligado(self):
        """Garante o servidor Ollama de pé (o modelo carrega sob demanda)."""
        if not systemd.esta_ativo(self.unit):
            systemd.iniciar(self.unit)

    def desligar(self):
        """Descarrega os modelos da memória (libera RAM), sem derrubar o serviço."""
        for modelo in self.modelos:
            subprocess.run(["ollama", "stop", modelo], check=False)
        return True
