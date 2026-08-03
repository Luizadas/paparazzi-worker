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
from core.services import WatcherController, PosterController, SistemaController


def dashboard(request):
    limite = timezone.now() - timedelta(hours=24)
    videos = (Video.objects
              .filter(criado_em__gte=limite)
              .select_related("canal")
              .prefetch_related("posts")[:200])
    contexto = {
        "status": SistemaController().status(),
        "canais": Canal.objects.all(),
        "videos": videos,
        "n_publicados_24h": Post.objects.filter(
            status=Post.Status.PUBLICADO, postado_em__gte=limite).count(),
        "n_fila_pendente": Post.objects.filter(status=Post.Status.PENDENTE).count(),
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
