"""
Roda o editor como daemon (alvo do serviço systemd paparazzi-editor).

Parada graciosa: encerra apenas ENTRE vídeos (o vídeo em edição termina), seja
pelo botão Desligar do painel ('parar_editor' em EstadoSistema) ou por SIGTERM.
AutoEditor começa SEMPRE desmarcado a cada (re)início.
"""

import signal
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import EstadoSistema


class Command(BaseCommand):
    help = "Roda o editor (processa a fila de edição) em modo daemon."

    def handle(self, *args, **opts):
        repo = Path(settings.REPO_DIR)
        sys.path.insert(0, str(repo / "mirror_clips"))
        import editor as editor_mod

        self._sigterm = False

        def _on_sigterm(signum, frame):
            self.stdout.write("Recebido SIGTERM — encerrando após o vídeo atual...")
            self._sigterm = True

        signal.signal(signal.SIGTERM, _on_sigterm)
        signal.signal(signal.SIGINT, _on_sigterm)

        def deve_parar():
            if self._sigterm:
                return True
            obj = EstadoSistema.objects.filter(chave="parar_editor").first()
            return bool(obj and obj.valor == "1")

        EstadoSistema.objects.update_or_create(chave="editor_ligado",
                                               defaults={"valor": "1"})
        EstadoSistema.objects.update_or_create(chave="autoeditor",
                                               defaults={"valor": "0"})
        try:
            editor_mod.modo_daemon(deve_parar=deve_parar)
        finally:
            EstadoSistema.objects.update_or_create(chave="editor_ligado",
                                                   defaults={"valor": "0"})
            self.stdout.write(self.style.SUCCESS("Editor finalizado."))
