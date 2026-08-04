"""
PosterController — liga/desliga o poster (publicador) via systemd, com shutdown
gracioso (encerra apenas ENTRE posts; o post em andamento termina).

O poster publica captions já geradas pelo coletor, então NÃO precisa do Ollama.
Quando ligado sem o watcher, ele simplesmente drena a FILA de vídeos já
processados (status 'pendente') e fica aguardando novos.
"""

from django.conf import settings

from core.models import EstadoSistema
from core.services import systemd


class PosterController:
    CHAVE_LIGADO = "poster_ligado"
    CHAVE_PARAR = "parar_poster"

    def __init__(self, unit=None):
        self.unit = unit or settings.POSTER_SYSTEMD_UNIT

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

    def parada_solicitada(self):
        return self._get(self.CHAVE_PARAR, "0") == "1"

    def autoposter(self):
        return self._get("autoposter", "0") == "1"

    def set_autoposter(self, ligado):
        self._set("autoposter", "1" if ligado else "0")

    def privacidade(self):
        return self._get("privacidade", "SELF_ONLY")

    def set_privacidade(self, nivel):
        self._set("privacidade", nivel)

    def status(self):
        est = self.estado()
        return {
            "instalado": self.instalado(),
            "ligado": est == "active",
            "estado": est,
            "desligando": est == "deactivating" or (
                self.parada_solicitada() and est != "inactive"),
            "autoposter": self.autoposter(),
            "privacidade": self.privacidade(),
        }

    def ligar(self):
        """Sobe o poster; ele começa a postar a fila de já-processados."""
        self._set(self.CHAVE_PARAR, "0")
        ok = systemd.iniciar(self.unit)
        self._set(self.CHAVE_LIGADO, "1" if ok else "0")
        return ok

    def desligar(self):
        """Parada graciosa (não bloqueia): termina o post atual e sai."""
        self._set(self.CHAVE_PARAR, "1")
        return systemd.parar(self.unit, no_block=True)
