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


def iniciar(unit):
    return systemctl("start", unit).returncode == 0


def parar(unit):
    """Para o serviço. systemd envia SIGTERM e AGUARDA até TimeoutStopSec,
    então dá tempo do processo terminar o vídeo em andamento antes de sair."""
    return systemctl("stop", unit).returncode == 0
