"""
Roda o poster como daemon (alvo do serviço systemd paparazzi-poster).

Parada graciosa: encerra apenas ENTRE posts (o post em andamento termina),
seja pelo botão Desligar do painel ('parar_poster' em EstadoSistema) ou por
SIGTERM (systemctl stop).
"""

import signal
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import EstadoSistema


class Command(BaseCommand):
    help = "Roda o poster em modo daemon com parada graciosa."

    def add_arguments(self, parser):
        parser.add_argument("--modo", default="selenium",
                            choices=["auto", "api", "selenium"],
                            help="Modo de postagem (default selenium).")

    def handle(self, *args, **opts):
        repo = Path(settings.REPO_DIR)
        sys.path.insert(0, str(repo / "mirror_clips"))
        import poster as poster_mod

        self._sigterm = False

        def _on_sigterm(signum, frame):
            self.stdout.write("Recebido SIGTERM — encerrando após o post atual...")
            self._sigterm = True

        signal.signal(signal.SIGTERM, _on_sigterm)
        signal.signal(signal.SIGINT, _on_sigterm)

        def deve_parar():
            if self._sigterm:
                return True
            obj = EstadoSistema.objects.filter(chave="parar_poster").first()
            return bool(obj and obj.valor == "1")

        EstadoSistema.objects.update_or_create(chave="poster_ligado",
                                               defaults={"valor": "1"})
        try:
            poster_mod.modo_daemon(modo=opts["modo"], deve_parar=deve_parar)
        finally:
            EstadoSistema.objects.update_or_create(chave="poster_ligado",
                                                   defaults={"valor": "0"})
            self.stdout.write(self.style.SUCCESS("Poster finalizado."))
