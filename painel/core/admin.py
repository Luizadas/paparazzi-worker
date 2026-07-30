from django.contrib import admin

from core.models import (Canal, Video, Post, Job, ConsumidorLLM, EstadoSistema)


@admin.register(Canal)
class CanalAdmin(admin.ModelAdmin):
    list_display = ("nome", "youtube_id", "ativo", "min_views", "max_age_days", "criado_em")
    list_filter = ("ativo",)
    search_fields = ("nome", "youtube_id")
    list_editable = ("ativo", "min_views", "max_age_days")


class PostInline(admin.TabularInline):
    model = Post
    extra = 0
    fields = ("plataforma", "status", "caption", "url_publicada", "postado_em")
    readonly_fields = ("postado_em",)


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ("video_id", "canal", "status", "versao_sistema", "faixa_total",
                    "arquivo_ok", "views", "criado_em")
    list_filter = ("status", "versao_sistema", "arquivo_removido", "canal")
    search_fields = ("video_id", "titulo")
    readonly_fields = ("criado_em", "atualizado_em")
    inlines = [PostInline]

    @admin.display(boolean=True, description="Arquivo")
    def arquivo_ok(self, obj):
        return not obj.arquivo_removido and bool(obj.arquivo_local)

    @admin.display(boolean=True, description="Tarja full")
    def faixa_total(self, obj):
        return bool((obj.deteccao or {}).get("faixa_total"))


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("video", "plataforma", "status", "privacidade", "postado_em")
    list_filter = ("status", "plataforma", "privacidade")
    search_fields = ("video__video_id", "caption")
    readonly_fields = ("criado_em", "atualizado_em")


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("tipo", "video", "status", "criado_em", "finalizado_em")
    list_filter = ("tipo", "status")


@admin.register(ConsumidorLLM)
class ConsumidorLLMAdmin(admin.ModelAdmin):
    list_display = ("nome", "ativo", "heartbeat")
    list_filter = ("ativo",)


@admin.register(EstadoSistema)
class EstadoSistemaAdmin(admin.ModelAdmin):
    list_display = ("chave", "valor", "atualizado_em")
    search_fields = ("chave",)
