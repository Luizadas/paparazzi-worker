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
                               canal_youtube_id=None, video_id=None, transcricao=""):
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
        defaults = {
            "status": Video.Status.PROCESSADO,
            "versao_sistema": versao,
            "deteccao": deteccao or {},
            "transcricao": transcricao or "",
            "arquivo_local": arquivo or "",
            "arquivo_removido": False,
            "tamanho_bytes": tamanho,
        }
        if canal:
            defaults["canal"] = canal
        video, _ = Video.objects.update_or_create(video_id=vid, defaults=defaults)
        # Título e link da FONTE são do watcher (título do post no canal original).
        # A edição NÃO pode sobrescrevê-los — o painel mostra esse título na aba
        # Edições e linka para o vídeo original. Só preenchemos o que estiver vazio.
        faltando = []
        if titulo and not video.titulo:
            video.titulo = titulo; faltando.append("titulo")
        if not video.url_origem:
            video.url_origem = f"https://www.youtube.com/watch?v={vid}"
            faltando.append("url_origem")
        if faltando:
            video.save(update_fields=faltando + ["atualizado_em"])
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
        video = Video.objects.filter(video_id=vid).first()
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


def marcar_post_status(arquivo, status, url="", post_id=None):
    """Atualiza o Post para publicado/falhou/postando. Em 'publicado', carimba
    postado_em (base da retenção de 24h) e marca o Video como postado.
    Se post_id for informado, atualiza esse post diretamente (mais preciso)."""
    if not _setup_django():
        return None
    try:
        from django.utils import timezone
        from core.models import Video, Post
        if post_id is not None:
            post = Post.objects.select_related("video").filter(id=post_id).first()
            if not post:
                return None
            video = post.video
        else:
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


# -- FILA DE POSTAGEM no banco (fonte única) --------------------------------
def enfileirar(arquivo, titulo="", caption="", versao="", privacidade="SELF_ONLY"):
    """Coloca o vídeo na fila = cria um Post 'pendente' no banco (idempotente)."""
    return registrar_post_pendente(arquivo, caption=caption, versao=versao,
                                   privacidade=privacidade)


def reivindicar_proximo_post():
    """Reivindica ATOMICAMENTE o próximo Post 'pendente' (o mais antigo), marca
    'postando' e o retorna. Usa SELECT ... FOR UPDATE SKIP LOCKED para permitir
    concorrência sem duplo-claim. Retorna dict {post_id, video_path, titulo,
    caption} ou None se a fila estiver vazia (ou o banco indisponível)."""
    if not _setup_django():
        return None
    try:
        from django.db import transaction
        from core.models import Post
        with transaction.atomic():
            post = (Post.objects.select_for_update(skip_locked=True)
                    .filter(status=Post.Status.PENDENTE)
                    .select_related("video")
                    .order_by("criado_em")
                    .first())
            if not post:
                return None
            post.status = Post.Status.POSTANDO
            post.save(update_fields=["status", "atualizado_em"])
            return {
                "post_id": post.id,
                "video_path": post.video.arquivo_local,
                "titulo": post.video.titulo,
                "caption": post.caption,
                "status": "postando",
            }
    except Exception as e:
        print(f"⚠️  db_bridge.reivindicar_proximo_post: {e}")
        return None


def contar_pendentes():
    if not _setup_django():
        return 0
    try:
        from core.models import Post
        return Post.objects.filter(status=Post.Status.PENDENTE).count()
    except Exception:
        return 0


# -- CANDIDATOS / FILA DE EDIÇÃO no banco -----------------------------------
def registrar_candidato(video_id, canal_youtube_id=None, canal_nome="", titulo="",
                        url="", views=0, publicado_origem=None):
    """Registra (ou atualiza) um vídeo que PASSOU no filtro do watcher como
    CANDIDATO à edição (status 'detectado'). Não regride o status de vídeos que já
    estão em edição/processados. Guarda views, canal e data de publicação no canal."""
    if not _setup_django():
        return None
    try:
        from core.models import Canal, Video
        canal = None
        if canal_youtube_id:
            canal, _ = Canal.objects.get_or_create(
                youtube_id=canal_youtube_id, defaults={"nome": canal_nome or ""})
        video, criado = Video.objects.get_or_create(
            video_id=video_id,
            defaults={
                "canal": canal, "titulo": titulo or "",
                "url_origem": url or f"https://www.youtube.com/watch?v={video_id}",
                "views": views or 0, "publicado_origem": publicado_origem,
                "status": Video.Status.DETECTADO,
            },
        )
        if not criado:
            campos = []
            if views:
                video.views = views; campos.append("views")
            if publicado_origem and not video.publicado_origem:
                video.publicado_origem = publicado_origem; campos.append("publicado_origem")
            if canal and not video.canal:
                video.canal = canal; campos.append("canal")
            if campos:
                video.save(update_fields=campos + ["atualizado_em"])
        return video.id
    except Exception as e:
        print(f"⚠️  db_bridge.registrar_candidato: {e}")
        return None


def marcar_para_editar(video_pk):
    """Botão 'Editar' do painel: promove um candidato (detectado) para a fila de
    edição (fila_edicao). Retorna True se promoveu."""
    if not _setup_django():
        return False
    try:
        from core.models import Video
        n = (Video.objects.filter(id=video_pk, status=Video.Status.DETECTADO)
             .update(status=Video.Status.FILA_EDICAO))
        return n > 0
    except Exception as e:
        print(f"⚠️  db_bridge.marcar_para_editar: {e}")
        return False


def reivindicar_proxima_edicao(auto=False):
    """Reivindica ATOMICAMENTE o próximo vídeo a editar → 'processando'.
    auto=False: só os selecionados (fila_edicao). auto=True (AutoEditor): também
    puxa candidatos (detectado). Retorna {video_id, url} ou None."""
    if not _setup_django():
        return None
    try:
        from django.db import transaction
        from core.models import Video
        statuses = [Video.Status.FILA_EDICAO]
        if auto:
            statuses.append(Video.Status.DETECTADO)
        with transaction.atomic():
            v = (Video.objects.select_for_update(skip_locked=True)
                 .filter(status__in=statuses).order_by("criado_em").first())
            if not v:
                return None
            v.status = Video.Status.PROCESSANDO
            v.save(update_fields=["status", "atualizado_em"])
            return {"video_id": v.video_id,
                    "url": v.url_origem or f"https://www.youtube.com/watch?v={v.video_id}"}
    except Exception as e:
        print(f"⚠️  db_bridge.reivindicar_proxima_edicao: {e}")
        return None


def canais_monitorados():
    """IDs de canal do YouTube cadastrados no painel — é o banco que manda quais
    canais o detector varre, não uma constante no código."""
    if not _setup_django():
        return []
    try:
        from core.models import Canal
        return [c.youtube_id for c in Canal.objects.all() if c.youtube_id]
    except Exception as e:
        print(f"⚠️  db_bridge.canais_monitorados: {e}")
        return []


def retomar_travados(quem="editor"):
    """
    Devolve à fila o trabalho que ficou preso num estado TRANSITÓRIO por uma
    parada não-graciosa (queda de energia, kill -9, reboot). É chamada na PARTIDA
    de cada serviço, quando por definição não há nada em andamento.

    Sem isso, um vídeo interrompido em 'processando' — e um post interrompido em
    'postando' — não voltavam a ser pegos por ninguém: ficavam parados para
    sempre e sumiam da fila sem nunca virar arquivo. Era esse o vídeo "perdido"
    depois de cada queda de energia.

    quem='editor' → vídeos 'processando' voltam a 'fila_edicao'.
    quem='poster' → posts 'postando' voltam a 'pendente'.
    Devolve quantos itens foram devolvidos à fila.
    """
    if not _setup_django():
        return 0
    try:
        from core.models import Video, Post
        if quem == "poster":
            n = (Post.objects.filter(status=Post.Status.POSTANDO)
                 .update(status=Post.Status.PENDENTE))
            if n:
                print(f"↩️  {n} post(s) presos em 'postando' devolvidos à fila.")
        else:
            n = (Video.objects.filter(status=Video.Status.PROCESSANDO)
                 .update(status=Video.Status.FILA_EDICAO))
            if n:
                print(f"↩️  {n} vídeo(s) presos em 'processando' devolvidos "
                      f"à fila de edição.")
        return n
    except Exception as e:
        print(f"⚠️  db_bridge.retomar_travados: {e}")
        return 0


def marcar_edicao_falhou(video_id):
    if not _setup_django():
        return
    try:
        from core.models import Video
        Video.objects.filter(video_id=video_id).update(status=Video.Status.ERRO)
    except Exception as e:
        print(f"⚠️  db_bridge.marcar_edicao_falhou: {e}")


def contar_por_status(status):
    if not _setup_django():
        return 0
    try:
        from core.models import Video
        return Video.objects.filter(status=status).count()
    except Exception:
        return 0


# -- estado do sistema (flags simples) --------------------------------------
def get_estado(chave, default=""):
    if not _setup_django():
        return default
    try:
        from core.models import EstadoSistema
        obj = EstadoSistema.objects.filter(chave=chave).first()
        return obj.valor if obj else default
    except Exception:
        return default


def set_estado(chave, valor):
    if not _setup_django():
        return
    try:
        from core.models import EstadoSistema
        EstadoSistema.objects.update_or_create(chave=chave, defaults={"valor": str(valor)})
    except Exception as e:
        print(f"⚠️  db_bridge.set_estado: {e}")


def autoposter_ligado():
    """AutoPoster: quando ligado, o poster puxa o próximo da fila. Default '0'."""
    return get_estado("autoposter", "0") == "1"


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
