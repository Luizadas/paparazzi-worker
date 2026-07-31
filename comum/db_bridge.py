"""
Ponte entre os scripts do mirror (coletor/poster/watcher) e o banco de controle
(Postgres via Django ORM). Configura o Django sob demanda e expõe funções simples.

Tudo é best-effort: se o banco estiver indisponível, as funções apenas logam e
seguem — o pipeline do mirror NÃO pode quebrar por causa do controle.
"""

import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PAINEL = _REPO / "painel"
_ready = False


def _setup_django():
    global _ready
    if _ready:
        return True
    try:
        from django.conf import settings as dj
        if not dj.configured:
            if _PAINEL.as_posix() not in sys.path:
                sys.path.insert(0, _PAINEL.as_posix())
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "painel.settings")
            import django
            django.setup()
        _ready = True
        return True
    except Exception as e:
        print(f"⚠️  db_bridge: Django indisponível ({e}). Seguindo sem gravar no banco.")
        return False


def video_id_de(nome):
    base = os.path.splitext(os.path.basename(nome))[0]
    return re.sub(r"_\d{8}_\d{6}.*$", "", base)


def registrar_video_processado(arquivo, deteccao=None, versao="", titulo="",
                               canal_youtube_id=None, video_id=None):
    """Upsert de um Video (status=processado) após o coletor gerar o _final.mp4."""
    if not _setup_django():
        return None
    try:
        from core.models import Canal, Video
        vid = video_id or video_id_de(arquivo)
        canal = None
        if canal_youtube_id:
            canal, _ = Canal.objects.get_or_create(youtube_id=canal_youtube_id)
        tamanho = os.path.getsize(arquivo) if arquivo and os.path.exists(arquivo) else None
        video, _ = Video.objects.update_or_create(
            video_id=vid, versao_sistema=versao,
            defaults={
                "canal": canal, "titulo": titulo or "",
                "status": Video.Status.PROCESSADO,
                "deteccao": deteccao or {},
                "arquivo_local": arquivo or "",
                "arquivo_removido": False,
                "tamanho_bytes": tamanho,
            },
        )
        return video.id
    except Exception as e:
        print(f"⚠️  db_bridge.registrar_video_processado: {e}")
        return None


def registrar_post_pendente(arquivo, caption="", versao="", privacidade="SELF_ONLY"):
    """Cria (ou reaproveita) um Post pendente ligado ao Video daquele arquivo."""
    if not _setup_django():
        return None
    try:
        from core.models import Video, Post
        vid = video_id_de(arquivo)
        video = (Video.objects.filter(video_id=vid, versao_sistema=versao).first()
                 or Video.objects.filter(video_id=vid).order_by("-criado_em").first())
        if not video:
            return None
        post, _ = Post.objects.get_or_create(
            video=video, plataforma=Post.Plataforma.TIKTOK,
            status__in=[Post.Status.PENDENTE, Post.Status.POSTANDO],
            defaults={"caption": caption, "privacidade": privacidade},
        )
        if caption and not post.caption:
            post.caption = caption
            post.save(update_fields=["caption"])
        return post.id
    except Exception as e:
        print(f"⚠️  db_bridge.registrar_post_pendente: {e}")
        return None


def marcar_post_status(arquivo, status, url=""):
    """Atualiza o Post do arquivo para publicado/falhou/postando. Em 'publicado',
    carimba postado_em (base da retenção de 24h) e marca o Video como postado."""
    if not _setup_django():
        return None
    try:
        from django.utils import timezone
        from core.models import Video, Post
        vid = video_id_de(arquivo)
        video = (Video.objects.filter(arquivo_local=arquivo).first()
                 or Video.objects.filter(video_id=vid).order_by("-criado_em").first())
        if not video:
            return None
        post = video.posts.order_by("-criado_em").first()
        if not post:
            post = Post.objects.create(video=video)
        mapa = {"publicado": Post.Status.PUBLICADO, "falhou": Post.Status.FALHOU,
                "postando": Post.Status.POSTANDO}
        post.status = mapa.get(status, post.status)
        if status == "publicado":
            post.postado_em = timezone.now()
            post.url_publicada = url or post.url_publicada
            video.status = Video.Status.POSTADO
            video.save(update_fields=["status", "atualizado_em"])
        post.save()
        return post.id
    except Exception as e:
        print(f"⚠️  db_bridge.marcar_post_status: {e}")
        return None


# -- controle de LLM (para o shutdown gracioso) -----------------------------
def llm_adquirir(consumidor, garantir=True):
    if not _setup_django():
        return
    try:
        from core.services import LLMController
        LLMController().adquirir(consumidor, garantir=garantir)
    except Exception as e:
        print(f"⚠️  db_bridge.llm_adquirir: {e}")


def llm_liberar(consumidor):
    if not _setup_django():
        return
    try:
        from core.services import LLMController
        LLMController().liberar(consumidor)
    except Exception as e:
        print(f"⚠️  db_bridge.llm_liberar: {e}")
