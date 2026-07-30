from django.urls import path

from core import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("watcher/ligar", views.watcher_ligar, name="watcher_ligar"),
    path("watcher/desligar", views.watcher_desligar, name="watcher_desligar"),
    path("canal/adicionar", views.canal_adicionar, name="canal_adicionar"),
    path("canal/<int:canal_id>/remover", views.canal_remover, name="canal_remover"),
]
