"""
Schema de controle (Postgres) — modelado já pensando no sistema robusto de CORTES.

Entidades:
  Canal   -> fonte de vídeos (YouTube). Add/remover pelo painel.
  Video   -> um vídeo baixado/processado (proveniência + arquivo local + detecção).
  Post    -> uma publicação (TikTok...). A caption NUNCA é apagada.
  Job     -> unidade de trabalho da pipeline (detecção/coleta/processo/postagem/corte).
  ConsumidorLLM -> contagem de uso das LLMs entre processos (p/ shutdown gracioso).
  EstadoSistema -> flags de controle (watcher_ligado, parar_solicitado...).

Regra de retenção: após 24h da postagem, o ARQUIVO local do vídeo é apagado
(arquivo_removido=True), mas os metadados e a caption permanecem.
"""

from django.db import models
from django.utils import timezone


class Canal(models.Model):
    youtube_id = models.CharField(max_length=64, unique=True)
    nome = models.CharField(max_length=200, blank=True)
    url = models.URLField(blank=True)
    ativo = models.BooleanField(default=True)          # entra ou não na varredura do watcher
    min_views = models.PositiveIntegerField(default=30000)
    max_age_days = models.PositiveIntegerField(default=7)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Canal"
        verbose_name_plural = "Canais"
        ordering = ["nome", "youtube_id"]

    def __str__(self):
        return self.nome or self.youtube_id


class Video(models.Model):
    class Status(models.TextChoices):
        DETECTADO = "detectado", "Detectado"
        BAIXANDO = "baixando", "Baixando"
        PROCESSANDO = "processando", "Processando"
        PROCESSADO = "processado", "Processado"
        POSTADO = "postado", "Postado"
        ERRO = "erro", "Erro"

    video_id = models.CharField(max_length=64, db_index=True)   # id do YouTube
    canal = models.ForeignKey(Canal, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="videos")
    titulo = models.CharField(max_length=300, blank=True)
    url_origem = models.URLField(blank=True)

    views = models.BigIntegerField(default=0)
    publicado_origem = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DETECTADO)
    versao_sistema = models.CharField(max_length=20, blank=True)   # ex.: "1.2.0"
    deteccao = models.JSONField(null=True, blank=True)             # resultado do OCR/tarja

    arquivo_local = models.CharField(max_length=500, blank=True)   # caminho do _final.mp4
    arquivo_removido = models.BooleanField(default=False)          # True após retenção de 24h
    tamanho_bytes = models.BigIntegerField(null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vídeo"
        verbose_name_plural = "Vídeos"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(fields=["video_id", "versao_sistema"],
                                    name="uniq_video_por_versao"),
        ]

    def __str__(self):
        return f"{self.video_id} ({self.status})"


class Post(models.Model):
    class Plataforma(models.TextChoices):
        TIKTOK = "tiktok", "TikTok"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        POSTANDO = "postando", "Postando"
        PUBLICADO = "publicado", "Publicado"
        FALHOU = "falhou", "Falhou"

    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name="posts")
    plataforma = models.CharField(max_length=20, choices=Plataforma.choices,
                                  default=Plataforma.TIKTOK)
    caption = models.TextField(blank=True)          # legenda do post — NUNCA apagada
    privacidade = models.CharField(max_length=20, default="SELF_ONLY")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    url_publicada = models.URLField(blank=True)
    postado_em = models.DateTimeField(null=True, blank=True)
    erro = models.TextField(blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Post"
        verbose_name_plural = "Posts"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.plataforma}:{self.video.video_id} ({self.status})"


class Job(models.Model):
    class Tipo(models.TextChoices):
        DETECCAO = "deteccao", "Detecção"
        COLETA = "coleta", "Coleta"
        PROCESSAMENTO = "processamento", "Processamento"
        POSTAGEM = "postagem", "Postagem"
        CORTE = "corte", "Corte"          # futuro sistema de cortes

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        RODANDO = "rodando", "Rodando"
        CONCLUIDO = "concluido", "Concluído"
        FALHOU = "falhou", "Falhou"

    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    video = models.ForeignKey(Video, on_delete=models.CASCADE, null=True, blank=True,
                              related_name="jobs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    payload = models.JSONField(null=True, blank=True)
    erro = models.TextField(blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Job"
        verbose_name_plural = "Jobs"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.tipo} #{self.pk} ({self.status})"


class ConsumidorLLM(models.Model):
    """
    Contagem de uso das LLMs (Ollama) ENTRE processos, para o shutdown gracioso:
    o watcher/coletor e o poster registram-se aqui enquanto precisam do modelo.
    O LLMController só encerra o Ollama quando não há nenhum consumidor ativo.
    """
    nome = models.CharField(max_length=50, unique=True)   # ex.: "watcher", "poster"
    ativo = models.BooleanField(default=False)
    heartbeat = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Consumidor de LLM"
        verbose_name_plural = "Consumidores de LLM"

    def __str__(self):
        return f"{self.nome}: {'ativo' if self.ativo else 'ocioso'}"


class EstadoSistema(models.Model):
    """
    Chave/valor simples para flags de controle (ex.: watcher_ligado, parar_solicitado).
    Uma linha por chave. Evita depender de arquivos de flag espalhados.
    """
    chave = models.CharField(max_length=50, unique=True)
    valor = models.CharField(max_length=200, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Estado do sistema"
        verbose_name_plural = "Estado do sistema"

    def __str__(self):
        return f"{self.chave}={self.valor}"
