"""
Apaga os arquivos de vídeo postados há mais de RETENCAO_HORAS (mantém metadados).
Rodado periodicamente pelo systemd timer (paparazzi-retencao.timer).

    python manage.py aplicar_retencao          # aplica
    python manage.py aplicar_retencao --dry    # só mostra o que expiraria
"""

from django.core.management.base import BaseCommand
from django.db.utils import OperationalError

from core.services import RetencaoService


class Command(BaseCommand):
    help = "Aplica a política de retenção (apaga arquivos > 24h, mantém metadados)."

    def add_arguments(self, parser):
        parser.add_argument("--dry", action="store_true", help="Simula, sem apagar.")

    def handle(self, *args, **opts):
        try:
            resultados = RetencaoService().aplicar(dry_run=opts["dry"])
        except OperationalError as e:
            # Ex.: rodou no boot antes do Postgres subir. Não é erro fatal —
            # o timer horário roda de novo quando o banco estiver no ar.
            self.stdout.write(self.style.WARNING(
                f"Banco indisponível agora ({str(e).splitlines()[0]}). "
                "Pulando; o próximo ciclo tenta de novo."))
            return
        if not resultados:
            self.stdout.write("Nada a fazer — nenhum vídeo expirado.")
            return
        for video_id, res in resultados:
            self.stdout.write(f"{video_id}: {res}")
        self.stdout.write(self.style.SUCCESS(f"{len(resultados)} vídeo(s) processado(s)."))
