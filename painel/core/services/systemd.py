"""
Helper fino para falar com o systemd. Centraliza o prefixo do comando
(systemctl / sudo systemctl / systemctl --user) via settings.SYSTEMCTL, para
funcionar tanto com serviços de sistema quanto de usuário.
"""

import subprocess

from django.conf import settings


def _prefixo():
    return list(getattr(settings, "SYSTEMCTL", ["systemctl"]))


def systemctl(*args, check=False):
    """Executa `systemctl <args>` e devolve o CompletedProcess."""
    return subprocess.run(_prefixo() + list(args),
                          capture_output=True, text=True, check=check)


def esta_ativo(unit):
    return systemctl("is-active", "--quiet", unit).returncode == 0


def estado(unit):
    """String crua: active / inactive / deactivating / activating / failed..."""
    return (systemctl("is-active", unit).stdout or "").strip() or "unknown"


def iniciar(unit):
    return systemctl("start", unit).returncode == 0


def parar(unit, no_block=True):
    """Para o serviço. Por padrão NÃO bloqueia (--no-block): o systemd envia SIGTERM
    e aguarda TimeoutStopSec para o processo terminar o vídeo/post em andamento,
    mas o painel retorna na hora (a parada graciosa acontece em segundo plano).
    Sem --no-block, uma parada longa estoura o timeout do DBus e reporta erro à toa."""
    args = ["stop", unit] + (["--no-block"] if no_block else [])
    return systemctl(*args).returncode == 0
