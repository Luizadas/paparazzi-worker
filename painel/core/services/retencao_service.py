"""
RetencaoService — política de retenção dos vídeos postados.

Regra: passadas RETENCAO_HORAS (default 24h) da POSTAGEM, apaga o ARQUIVO local
do vídeo (o _final.mp4). Os METADADOS e a CAPTION permanecem no banco para o
controle no painel — nunca são apagados.
"""

import os

from django.conf import settings
from django.utils import timezone
from datetime import timedelta

from core.models import Video, Post


class RetencaoService:
    def __init__(self, horas=None):
        self.horas = horas if horas is not None else settings.RETENCAO_HORAS

    def _limite(self):
        return timezone.now() - timedelta(hours=self.horas)

    def videos_expirados(self):
        """Vídeos com post publicado há mais de N horas cujo arquivo ainda existe."""
        limite = self._limite()
        ids = (Post.objects
               .filter(status=Post.Status.PUBLICADO, postado_em__lt=limite)
               .values_list("video_id", flat=True))
        return Video.objects.filter(id__in=list(ids), arquivo_removido=False)

    def aplicar(self, dry_run=False):
        """Apaga os arquivos expirados. Retorna a lista de (video_id, resultado)."""
        resultados = []
        for video in self.videos_expirados():
            caminho = video.arquivo_local
            existe = bool(caminho) and os.path.exists(caminho)
            if dry_run:
                resultados.append((video.video_id, "expira" if existe else "sem-arquivo"))
                continue
            if existe:
                try:
                    os.remove(caminho)
                    resultado = "removido"
                except OSError as e:
                    resultado = f"erro:{e}"
            else:
                resultado = "arquivo-ausente"
            video.arquivo_removido = True          # metadados/caption ficam intactos
            video.save(update_fields=["arquivo_removido", "atualizado_em"])
            resultados.append((video.video_id, resultado))
        return resultados
