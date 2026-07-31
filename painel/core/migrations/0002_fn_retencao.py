"""
Instala a REGRA de retenção como função no Postgres:

  fn_videos_expirados(p_horas)   -> lista os vídeos cujo post foi publicado há mais
                                    de p_horas e cujo arquivo ainda não foi removido.
  fn_marcar_video_removido(p_id) -> marca arquivo_removido=true (metadados ficam).

A regra (o "o que expira") mora no banco; o serviço Python só apaga o arquivo em
disco e chama a função de marcação. Só roda no PostgreSQL (no-op em outros bancos).
"""

from django.db import migrations


SQL_UP = r"""
CREATE OR REPLACE FUNCTION fn_videos_expirados(p_horas integer DEFAULT 24)
RETURNS TABLE(id bigint, video_id varchar, arquivo_local varchar)
LANGUAGE sql STABLE AS $$
    SELECT v.id, v.video_id, v.arquivo_local
    FROM core_video v
    WHERE v.arquivo_removido = false
      AND coalesce(v.arquivo_local, '') <> ''
      AND EXISTS (
          SELECT 1 FROM core_post p
          WHERE p.video_id = v.id
            AND p.status = 'publicado'
            AND p.postado_em IS NOT NULL
            AND p.postado_em < now() - make_interval(hours => p_horas)
      );
$$;

CREATE OR REPLACE FUNCTION fn_marcar_video_removido(p_id bigint)
RETURNS void
LANGUAGE sql AS $$
    UPDATE core_video
       SET arquivo_removido = true, atualizado_em = now()
     WHERE id = p_id;
$$;
"""

SQL_DOWN = r"""
DROP FUNCTION IF EXISTS fn_videos_expirados(integer);
DROP FUNCTION IF EXISTS fn_marcar_video_removido(bigint);
"""


def criar_funcoes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(SQL_UP)


def remover_funcoes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(SQL_DOWN)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(criar_funcoes, remover_funcoes),
    ]
