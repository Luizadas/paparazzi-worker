"""
RetencaoService — executa a política de retenção dos vídeos postados.

A REGRA (quais vídeos expiraram após N horas) vive no Postgres, nas funções
`fn_videos_expirados(p_horas)` e `fn_marcar_video_removido(p_id)` (migration
0002_fn_retencao). Este serviço apenas ORQUESTRA: pergunta ao banco quem expirou,
apaga o ARQUIVO em disco e chama a função de marcação. Metadados e caption ficam.
"""

from django.conf import settings
from django.db import connection

import os


class RetencaoService:
    def __init__(self, horas=None):
        self.horas = horas if horas is not None else settings.RETENCAO_HORAS

    def videos_expirados(self):
        """Consulta a REGRA no banco (função fn_videos_expirados).
        Retorna lista de dicts: {id, video_id, arquivo_local}."""
        with connection.cursor() as cur:
            cur.execute("SELECT id, video_id, arquivo_local "
                        "FROM fn_videos_expirados(%s)", [self.horas])
            colunas = [c[0] for c in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    def _marcar_removido(self, video_pk):
        with connection.cursor() as cur:
            cur.execute("SELECT fn_marcar_video_removido(%s)", [video_pk])

    def aplicar(self, dry_run=False):
        """Apaga os arquivos expirados e marca no banco. Retorna [(video_id, resultado)]."""
        resultados = []
        for row in self.videos_expirados():
            caminho = row["arquivo_local"]
            existe = bool(caminho) and os.path.exists(caminho)
            if dry_run:
                resultados.append((row["video_id"], "expira" if existe else "sem-arquivo"))
                continue
            if existe:
                try:
                    os.remove(caminho)
                    resultado = "removido"
                except OSError as e:
                    resultado = f"erro:{e}"
            else:
                resultado = "arquivo-ausente"
            self._marcar_removido(row["id"])       # metadados/caption ficam intactos
            resultados.append((row["video_id"], resultado))
        return resultados
