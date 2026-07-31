from django.urls import path

from core import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("watcher/ligar", views.watcher_ligar, name="watcher_ligar"),
    path("watcher/desligar", views.watcher_desligar, name="watcher_desligar"),
    path("poster/ligar", views.poster_ligar, name="poster_ligar"),
    path("poster/desligar", views.poster_desligar, name="poster_desligar"),
    path("sistema/ligar", views.sistema_ligar, name="sistema_ligar"),
    path("sistema/desligar", views.sistema_desligar, name="sistema_desligar"),
    path("canal/adicionar", views.canal_adicionar, name="canal_adicionar"),
    path("canal/<int:canal_id>/remover", views.canal_remover, name="canal_remover"),
]
