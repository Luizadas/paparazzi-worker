"""
Painel de controle (local, sem login). Mostra os vídeos das últimas 24h e permite:
  - adicionar / remover canal
  - ligar / desligar o watcher (com shutdown gracioso das LLMs)
"""

import os
from datetime import timedelta

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Canal, Video, Post
from core.services import (WatcherController, PosterController, EditorController,
                           SistemaController)


def dashboard(request):
    limite = timezone.now() - timedelta(hours=24)
    videos = (Video.objects
              .filter(criado_em__gte=limite)
              .select_related("canal")
              .prefetch_related("posts")[:200])
    post_atual = (Post.objects.filter(status=Post.Status.POSTANDO)
                  .select_related("video").order_by("-atualizado_em").first())
    contexto = {
        "status": SistemaController().status(),
        "canais": Canal.objects.all(),
        "videos": videos,
        "n_publicados_24h": Post.objects.filter(
            status=Post.Status.PUBLICADO, postado_em__gte=limite).count(),
        "n_fila_pendente": Post.objects.filter(status=Post.Status.PENDENTE).count(),
        "n_postando": Post.objects.filter(status=Post.Status.POSTANDO).count(),
        "post_atual": post_atual.video.video_id if post_atual else None,
    }
    return render(request, "core/dashboard.html", contexto)


@require_POST
def watcher_ligar(request):
    ok = WatcherController().ligar()
    messages.success(request, "Watcher ligado — produzindo vídeos." if ok
                     else "Falha ao ligar o watcher (verifique o serviço systemd).")
    return redirect("dashboard")


@require_POST
def watcher_desligar(request):
    ok = WatcherController().desligar()
    messages.success(request, "Watcher desligando graciosamente (termina o vídeo atual)." if ok
                     else "Falha ao desligar o watcher.")
    return redirect("dashboard")


_MSG_NAO_INSTALADO = ("Serviço do poster não instalado. Rode uma vez: "
                      "sudo bash systemd/instalar_servicos.sh")


@require_POST
def poster_ligar(request):
    pc = PosterController()
    if not pc.instalado():
        messages.error(request, _MSG_NAO_INSTALADO)
        return redirect("dashboard")
    ok = pc.ligar()
    messages.success(request, "Poster ligado — postando a fila de já-processados." if ok
                     else "Falha ao ligar o poster (veja: journalctl -u paparazzi-poster).")
    return redirect("dashboard")


@require_POST
def poster_desligar(request):
    pc = PosterController()
    if not pc.instalado():
        messages.error(request, _MSG_NAO_INSTALADO)
        return redirect("dashboard")
    ok = pc.desligar()
    messages.success(request, "Poster desligando graciosamente (termina o post atual)." if ok
                     else "Falha ao desligar o poster.")
    return redirect("dashboard")


@require_POST
def autoposter_toggle(request):
    ligado = request.POST.get("autoposter") == "on"
    PosterController().set_autoposter(ligado)
    messages.success(request, "AutoPoster LIGADO — o poster vai puxar a fila." if ligado
                     else "AutoPoster desligado — o poster não puxa a fila.")
    return redirect("dashboard")


@require_POST
def editor_ligar(request):
    ec = EditorController()
    if not ec.instalado():
        messages.error(request, "Serviço do editor não instalado. Rode: "
                                "sudo bash systemd/instalar_servicos.sh")
        return redirect("edicoes")
    ok = ec.ligar()
    messages.success(request, "Editor ligado." if ok else "Falha ao ligar o editor.")
    return redirect("edicoes")


@require_POST
def editor_desligar(request):
    ok = EditorController().desligar()
    messages.success(request, "Editor desligando (termina o vídeo atual)." if ok
                     else "Falha ao desligar o editor.")
    return redirect("edicoes")


@require_POST
def autoeditor_toggle(request):
    ligado = request.POST.get("autoeditor") == "on"
    EditorController().set_autoeditor(ligado)
    messages.success(request, "AutoEditor LIGADO — o editor puxa todos os candidatos."
                     if ligado else "AutoEditor desligado — só edita os selecionados.")
    return redirect("edicoes")


def edicoes_lista(request):
    candidatos = (Video.objects
                  .filter(status__in=[Video.Status.DETECTADO, Video.Status.FILA_EDICAO,
                                      Video.Status.PROCESSANDO])
                  .select_related("canal").order_by("status", "-criado_em"))
    return render(request, "core/edicoes.html", {
        "candidatos": candidatos,
        "status": SistemaController().status(),
    })


@require_POST
def editar_video(request, video_id):
    v = get_object_or_404(Video, id=video_id)
    n = (Video.objects.filter(id=v.id, status=Video.Status.DETECTADO)
         .update(status=Video.Status.FILA_EDICAO))
    if n:
        messages.success(request, f"{v.video_id} adicionado à fila de edição.")
    else:
        messages.error(request, "Não foi possível selecionar (já está na fila/edição?).")
    return redirect("edicoes")


@require_POST
def sistema_ligar(request):
    SistemaController().ligar_tudo()
    messages.success(request, "Sistema LIGADO — watcher e poster no ar.")
    return redirect("dashboard")


@require_POST
def sistema_desligar(request):
    SistemaController().desligar_tudo()
    messages.success(request, "Sistema DESLIGANDO — watcher e poster encerram "
                              "graciosamente (terminam o item atual); a LLM é liberada.")
    return redirect("dashboard")


@require_POST
def canal_adicionar(request):
    youtube_id = (request.POST.get("youtube_id") or "").strip()
    nome = (request.POST.get("nome") or "").strip()
    if not youtube_id:
        messages.error(request, "Informe o ID do canal do YouTube.")
        return redirect("dashboard")
    canal, criado = Canal.objects.get_or_create(
        youtube_id=youtube_id,
        defaults={"nome": nome, "url": f"https://www.youtube.com/channel/{youtube_id}"},
    )
    if not criado and nome:
        canal.nome = nome
        canal.save(update_fields=["nome"])
    messages.success(request, f"Canal {'adicionado' if criado else 'atualizado'}: {canal}.")
    return redirect("dashboard")


@require_POST
def canal_remover(request, canal_id):
    Canal.objects.filter(id=canal_id).delete()
    messages.success(request, "Canal removido.")
    return redirect("dashboard")


# ─────────────────────────────────────────────
#  Aba: Lista de vídeos (controle das postagens)
# ─────────────────────────────────────────────

def _post_do_video(video):
    """O Post principal do vídeo (o mais recente). Cria um vazio se não houver."""
    return video.posts.order_by("-criado_em").first()


def _arquivo_disponivel(video):
    return bool(video.arquivo_local and not video.arquivo_removido
               and os.path.exists(video.arquivo_local))


def videos_lista(request):
    videos = (Video.objects.select_related("canal")
              .prefetch_related("posts").order_by("-criado_em"))
    rows = []
    for v in videos:
        post = _post_do_video(v)
        rows.append({
            "v": v,
            "caption": post.caption if post else "",
            "post_status": post.get_status_display() if post else "—",
            "disponivel": _arquivo_disponivel(v),
            "tem_transcricao": bool(v.transcricao),
        })
    return render(request, "core/videos.html", {"rows": rows})


def video_ver(request, video_id):
    v = get_object_or_404(Video, id=video_id)
    if not _arquivo_disponivel(v):
        raise Http404("Arquivo do vídeo indisponível (expirado ou removido).")
    return FileResponse(open(v.arquivo_local, "rb"), content_type="video/mp4")


def video_download(request, video_id):
    v = get_object_or_404(Video, id=video_id)
    if not _arquivo_disponivel(v):
        raise Http404("Arquivo do vídeo indisponível (expirado ou removido).")
    return FileResponse(open(v.arquivo_local, "rb"), as_attachment=True,
                        filename=os.path.basename(v.arquivo_local))


@require_POST
def caption_editar(request, video_id):
    v = get_object_or_404(Video, id=video_id)
    nova = (request.POST.get("caption") or "").strip()
    post = _post_do_video(v) or Post.objects.create(video=v)
    post.caption = nova
    post.save(update_fields=["caption", "atualizado_em"])
    messages.success(request, "Legenda salva.")
    return redirect("videos")


@require_POST
def caption_regerar(request, video_id):
    v = get_object_or_404(Video, id=video_id)
    if not v.transcricao:
        messages.error(request, "Sem transcrição salva para este vídeo — não dá para "
                                "regerar (vídeos antigos não têm transcrição).")
        return redirect("videos")
    from comum.legenda_ia import gerar_legenda_ia
    nova = gerar_legenda_ia(v.transcricao, titulo_original="")
    if not nova:
        messages.error(request, "A IA não retornou legenda (Ollama offline?). Tente de novo.")
        return redirect("videos")
    post = _post_do_video(v) or Post.objects.create(video=v)
    post.caption = nova
    post.save(update_fields=["caption", "atualizado_em"])
    messages.success(request, "Legenda regerada pela IA.")
    return redirect("videos")
