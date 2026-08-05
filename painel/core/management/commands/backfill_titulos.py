"""
Repõe o TÍTULO DO POST NO CANAL ORIGINAL (e o link da fonte) nos vídeos cujo
título ficou com o nome do arquivo.

Por que existe: até a v1.3.0 a edição gravava `titulo = nome_do_arquivo`
("bfAcE48SKDk 20260805 124549"), sobrescrevendo o título que o watcher havia lido
do YouTube. A aba Edições mostra esse título (e linka para a fonte), então as
linhas antigas precisam do valor real. Busca via yt-dlp (só metadados, sem
download). Idempotente: por padrão só toca no que está vazio ou auto-gerado.

Uso:
    python manage.py backfill_titulos            # só os auto-gerados/vazios
    python manage.py backfill_titulos --todos    # revalida todos
    python manage.py backfill_titulos --dry-run  # mostra sem gravar
"""

import re

from django.core.management.base import BaseCommand

from core.models import Canal, Video


def _auto_gerado(video):
    """True se o título parece ter vindo do nome do arquivo (id + timestamp)."""
    t = (video.titulo or "").strip()
    if not t:
        return True
    return bool(re.match(rf"^{re.escape(video.video_id)}[_ ]\d{{8}}[_ ]\d{{6}}",
                         t.replace(" ", "_")))


class Command(BaseCommand):
    help = "Repõe título/link do post original nos vídeos (via yt-dlp, sem download)."

    def add_arguments(self, parser):
        parser.add_argument("--todos", action="store_true",
                            help="revalida todos, não só os auto-gerados/vazios")
        parser.add_argument("--dry-run", action="store_true",
                            help="mostra o que faria, sem gravar")

    def handle(self, *args, **opts):
        import yt_dlp

        alvos = [v for v in Video.objects.all().order_by("criado_em")
                 if opts["todos"] or _auto_gerado(v)]
        if not alvos:
            self.stdout.write("Nada a corrigir.")
            return
        self.stdout.write(f"{len(alvos)} vídeo(s) para consultar no YouTube…")

        ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                                "skip_download": True, "noplaylist": True})
        ok = falhas = 0
        for v in alvos:
            try:
                info = ydl.extract_info(v.url_fonte, download=False)
            except Exception as e:
                falhas += 1
                self.stderr.write(f"  ✗ {v.video_id}: {type(e).__name__} — {e}")
                continue

            titulo = (info.get("title") or "").strip()
            campos = []
            if titulo and titulo != v.titulo:
                v.titulo = titulo[:300]; campos.append("titulo")
            if not v.url_origem:
                v.url_origem = v.url_fonte; campos.append("url_origem")
            if not v.publicado_origem and info.get("upload_date"):
                d = info["upload_date"]          # AAAAMMDD
                v.publicado_origem = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                campos.append("publicado_origem")
            if not v.canal and info.get("channel_id"):
                canal, _ = Canal.objects.get_or_create(
                    youtube_id=info["channel_id"],
                    defaults={"nome": info.get("channel") or ""})
                v.canal = canal; campos.append("canal")

            if not campos:
                self.stdout.write(f"  = {v.video_id}: já estava certo")
                continue
            if opts["dry_run"]:
                self.stdout.write(f"  ~ {v.video_id}: {', '.join(campos)} → {titulo[:60]!r}")
            else:
                v.save(update_fields=campos + ["atualizado_em"])
                self.stdout.write(f"  ✓ {v.video_id}: {titulo[:60]!r}")
            ok += 1

        resumo = f"{ok} atualizado(s), {falhas} falha(s)."
        self.stdout.write(self.style.SUCCESS(resumo if not opts["dry_run"]
                                             else f"[dry-run] {resumo}"))
