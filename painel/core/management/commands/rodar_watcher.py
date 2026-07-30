"""
Roda o watcher como daemon (alvo do serviço systemd paparazzi-watcher).

Integra o watcher do mirror com o controle do painel:
  - adquire a LLM ("watcher") no início (garante Ollama ligado);
  - roda o loop, parando GRACIOSAMENTE quando:
      * o painel marca 'parar_solicitado' (botão Desligar), ou
      * o systemd envia SIGTERM (systemctl stop) — só encerra ENTRE vídeos;
  - ao sair, libera a LLM; se o poster também já esvaziou a fila, o Ollama é
    encerrado (libera RAM). Caso contrário, segue vivo até o poster terminar.
"""

import os
import signal
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import EstadoSistema
from core.services import LLMController


class Command(BaseCommand):
    help = "Roda o watcher em modo daemon com parada graciosa e controle de LLM."

    def add_arguments(self, parser):
        parser.add_argument("--intervalo", type=int, default=300,
                            help="Segundos entre ciclos de verificação (default 300).")

    def handle(self, *args, **opts):
        # Torna o mirror_clips importável e importa o watcher (DB-agnóstico).
        repo = Path(settings.REPO_DIR)
        sys.path.insert(0, str(repo / "mirror_clips"))
        import watcher as watcher_mod

        self._sigterm = False

        def _on_sigterm(signum, frame):
            self.stdout.write("Recebido SIGTERM — encerrando após o vídeo atual...")
            self._sigterm = True

        signal.signal(signal.SIGTERM, _on_sigterm)
        signal.signal(signal.SIGINT, _on_sigterm)

        def deve_parar():
            if self._sigterm:
                return True
            obj = EstadoSistema.objects.filter(chave="parar_solicitado").first()
            return bool(obj and obj.valor == "1")

        llm = LLMController()
        llm.adquirir("watcher")
        EstadoSistema.objects.update_or_create(chave="watcher_ligado",
                                               defaults={"valor": "1"})
        try:
            watcher_mod.rodar_daemon(intervalo=opts["intervalo"], deve_parar=deve_parar)
        finally:
            EstadoSistema.objects.update_or_create(chave="watcher_ligado",
                                                   defaults={"valor": "0"})
            llm.liberar("watcher")   # encerra Ollama se ninguém mais usa
            self.stdout.write(self.style.SUCCESS("Watcher finalizado; LLM liberada."))
