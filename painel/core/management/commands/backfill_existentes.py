"""
Importa para o Postgres os dados que já existem em arquivo:
  - fila_postagem.json      (posts e captions)
  - data/processamentos.jsonl (proveniência: versão + detecção por vídeo)

Idempotente: pode rodar quantas vezes quiser. Cria o canal alvo (Cariani) e
associa os vídeos a ele.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.models import Canal, Video, Post

CANAL_ALVO = ("UCPX0gLduKAfgr-HJENa7CFw", "Renato Cariani")


def _video_id(nome):
    base = os.path.splitext(os.path.basename(nome))[0]
    return re.sub(r"_\d{8}_\d{6}.*$", "", base)


class Command(BaseCommand):
    help = "Importa fila_postagem.json e processamentos.jsonl para o banco."

    def handle(self, *args, **opts):
        repo = Path(settings.REPO_DIR)
        fila_path = repo / "mirror_clips" / "fila_postagem.json"
        prov_path = repo / "data" / "processamentos.jsonl"

        canal, _ = Canal.objects.get_or_create(
            youtube_id=CANAL_ALVO[0],
            defaults={"nome": CANAL_ALVO[1],
                      "url": f"https://www.youtube.com/channel/{CANAL_ALVO[0]}"})

        # Proveniência por video_id (última versão vista).
        prov = {}
        if prov_path.exists():
            for linha in prov_path.read_text(encoding="utf-8").splitlines():
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    r = json.loads(linha)
                    prov[r.get("video_id")] = r
                except json.JSONDecodeError:
                    continue

        n_video = n_post = 0
        itens = []
        if fila_path.exists():
            itens = json.loads(fila_path.read_text(encoding="utf-8"))

        for item in itens:
            arquivo = item.get("video_path", "")
            vid = _video_id(arquivo)
            pr = prov.get(vid, {})
            versao = pr.get("versao", "")
            existe = bool(arquivo) and os.path.exists(arquivo)

            video, criado = Video.objects.update_or_create(
                video_id=vid, versao_sistema=versao,
                defaults={
                    "canal": canal,
                    "titulo": item.get("titulo", ""),
                    "status": (Video.Status.POSTADO
                               if item.get("status") == "publicado"
                               else Video.Status.PROCESSADO),
                    "deteccao": {k: pr.get(k) for k in
                                 ("tem_legenda", "faixa_total", "cy", "meme")} if pr else {},
                    "arquivo_local": arquivo,
                    "arquivo_removido": not existe,
                    "tamanho_bytes": (os.path.getsize(arquivo) if existe else None),
                },
            )
            n_video += 1 if criado else 0

            status_map = {"publicado": Post.Status.PUBLICADO,
                          "falhou": Post.Status.FALHOU,
                          "postando": Post.Status.POSTANDO,
                          "pendente": Post.Status.PENDENTE}
            postado_em = None
            if item.get("status") == "publicado":
                postado_em = parse_datetime(item.get("atualizado_em") or "") or timezone.now()
                if postado_em and timezone.is_naive(postado_em):
                    postado_em = timezone.make_aware(postado_em)

            post, criado_p = Post.objects.get_or_create(
                video=video, plataforma=Post.Plataforma.TIKTOK,
                defaults={
                    "caption": item.get("caption", ""),
                    "status": status_map.get(item.get("status"), Post.Status.PENDENTE),
                    "postado_em": postado_em,
                    "privacidade": "SELF_ONLY",
                },
            )
            n_post += 1 if criado_p else 0

        self.stdout.write(self.style.SUCCESS(
            f"Backfill OK — canal: {canal}. Vídeos novos: {n_video}, posts novos: {n_post}. "
            f"Total no banco: {Video.objects.count()} vídeos, {Post.objects.count()} posts."))
