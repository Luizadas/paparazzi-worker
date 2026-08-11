"""
fn_videos_expirados passa a devolver TAMBÉM o postado_em do último post.

Para quê: a regra decide pelo BANCO, mas quem sabe se o arquivo em disco é o
mesmo que foi publicado é o DISCO. Ao reprocessar um vídeo já postado, o arquivo
novo é mais recente que a publicação — e apagá-lo é sempre errado, mesmo que a
regra "último post publicado há mais de N horas" esteja satisfeita.

Com o postado_em em mãos, o RetencaoService compara com o mtime do arquivo e
pula o que for mais novo que a publicação. É uma trava de segurança que não
depende de o enfileiramento ter acontecido (foi assim que 2zBrPQt5rSo e
Z11yJsFuLHM foram apagados em 10/08 20:00, recém-gerados).
"""

from django.db import migrations


SQL_UP = r"""
DROP FUNCTION IF EXISTS fn_videos_expirados(integer);
CREATE FUNCTION fn_videos_expirados(p_horas integer DEFAULT 24)
RETURNS TABLE(id bigint, video_id varchar, arquivo_local varchar,
              postado_em timestamptz)
LANGUAGE sql STABLE AS $$
    SELECT v.id, v.video_id, v.arquivo_local, ultimo.postado_em
    FROM core_video v
    CROSS JOIN LATERAL (
        SELECT p.status, p.postado_em
        FROM core_post p
        WHERE p.video_id = v.id
        ORDER BY p.criado_em DESC, p.id DESC
        LIMIT 1
    ) ultimo
    WHERE v.arquivo_removido = false
      AND coalesce(v.arquivo_local, '') <> ''
      AND ultimo.status = 'publicado'
      AND ultimo.postado_em IS NOT NULL
      AND ultimo.postado_em < now() - make_interval(hours => p_horas);
$$;
"""

SQL_DOWN = r"""
DROP FUNCTION IF EXISTS fn_videos_expirados(integer);
CREATE FUNCTION fn_videos_expirados(p_horas integer DEFAULT 24)
RETURNS TABLE(id bigint, video_id varchar, arquivo_local varchar)
LANGUAGE sql STABLE AS $$
    SELECT v.id, v.video_id, v.arquivo_local
    FROM core_video v
    CROSS JOIN LATERAL (
        SELECT p.status, p.postado_em
        FROM core_post p
        WHERE p.video_id = v.id
        ORDER BY p.criado_em DESC, p.id DESC
        LIMIT 1
    ) ultimo
    WHERE v.arquivo_removido = false
      AND coalesce(v.arquivo_local, '') <> ''
      AND ultimo.status = 'publicado'
      AND ultimo.postado_em IS NOT NULL
      AND ultimo.postado_em < now() - make_interval(hours => p_horas);
$$;
"""


def aplicar(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(SQL_UP)


def reverter(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(SQL_DOWN)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_fn_retencao_ultimo_post"),
    ]

    operations = [
        migrations.RunPython(aplicar, reverter),
    ]
