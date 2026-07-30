"""
Painel de controle (local, sem login). Mostra os vídeos das últimas 24h e permite:
  - adicionar / remover canal
  - ligar / desligar o watcher (com shutdown gracioso das LLMs)
"""

from datetime import timedelta

from django.contrib import messages
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Canal, Video, Post
from core.services import WatcherController


def dashboard(request):
    wc = WatcherController()
    limite = timezone.now() - timedelta(hours=24)
    videos = (Video.objects
              .filter(criado_em__gte=limite)
              .select_related("canal")
              .prefetch_related("posts")[:200])
    contexto = {
        "status": wc.status(),
        "canais": Canal.objects.all(),
        "videos": videos,
        "n_publicados_24h": Post.objects.filter(
            status=Post.Status.PUBLICADO, postado_em__gte=limite).count(),
    }
    return render(request, "core/dashboard.html", contexto)


@require_POST
def watcher_ligar(request):
    ok = WatcherController().ligar()
    messages.success(request, "Watcher ligado." if ok
                     else "Falha ao ligar o watcher (verifique o serviço systemd).")
    return redirect("dashboard")


@require_POST
def watcher_desligar(request):
    ok = WatcherController().desligar()
    messages.success(request, "Watcher desligado (aguardando terminar o vídeo atual; "
                              "LLM encerra quando a fila de posts esvaziar)." if ok
                     else "Falha ao desligar o watcher.")
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
