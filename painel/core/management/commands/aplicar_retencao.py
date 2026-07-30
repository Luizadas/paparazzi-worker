"""
Apaga os arquivos de vídeo postados há mais de RETENCAO_HORAS (mantém metadados).
Rodado periodicamente pelo systemd timer (paparazzi-retencao.timer).

    python manage.py aplicar_retencao          # aplica
    python manage.py aplicar_retencao --dry    # só mostra o que expiraria
"""

from django.core.management.base import BaseCommand

from core.services import RetencaoService


class Command(BaseCommand):
    help = "Aplica a política de retenção (apaga arquivos > 24h, mantém metadados)."

    def add_arguments(self, parser):
        parser.add_argument("--dry", action="store_true", help="Simula, sem apagar.")

    def handle(self, *args, **opts):
        resultados = RetencaoService().aplicar(dry_run=opts["dry"])
        if not resultados:
            self.stdout.write("Nada a fazer — nenhum vídeo expirado.")
            return
        for video_id, res in resultados:
            self.stdout.write(f"{video_id}: {res}")
        self.stdout.write(self.style.SUCCESS(f"{len(resultados)} vídeo(s) processado(s)."))
