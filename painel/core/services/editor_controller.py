"""
EditorController — liga/desliga o editor (processa candidatos) via systemd, com
shutdown gracioso (encerra só ENTRE vídeos). Espelha o PosterController.

AutoEditor: quando ligado, o editor também puxa candidatos automaticamente;
desligado, só edita os selecionados na aba. Reseta para '0' a cada início.
"""

from django.conf import settings

from core.models import EstadoSistema
from core.services import systemd


class EditorController:
    CHAVE_LIGADO = "editor_ligado"
    CHAVE_PARAR = "parar_editor"
    CHAVE_AUTO = "autoeditor"

    def __init__(self, unit=None):
        self.unit = unit or settings.EDITOR_SYSTEMD_UNIT

    def _set(self, chave, valor):
        EstadoSistema.objects.update_or_create(chave=chave, defaults={"valor": str(valor)})

    def _get(self, chave, default=""):
        obj = EstadoSistema.objects.filter(chave=chave).first()
        return obj.valor if obj else default

    def instalado(self):
        return systemd.existe(self.unit)

    def esta_ligado(self):
        return systemd.esta_ativo(self.unit)

    def estado(self):
        return systemd.estado(self.unit)

    def autoeditor(self):
        return self._get(self.CHAVE_AUTO, "0") == "1"

    def set_autoeditor(self, ligado):
        self._set(self.CHAVE_AUTO, "1" if ligado else "0")

    def status(self):
        est = self.estado()
        return {
            "instalado": self.instalado(),
            "ligado": est == "active",
            "estado": est,
            "desligando": est == "deactivating" or (
                self._get(self.CHAVE_PARAR, "0") == "1" and est != "inactive"),
            "autoeditor": self.autoeditor(),
        }

    def ligar(self):
        self._set(self.CHAVE_PARAR, "0")
        ok = systemd.iniciar(self.unit)
        self._set(self.CHAVE_LIGADO, "1" if ok else "0")
        return ok

    def desligar(self):
        self._set(self.CHAVE_PARAR, "1")
        return systemd.parar(self.unit, no_block=True)
