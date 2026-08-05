"""
Corrige a REGRA de retenção: olhar só o ÚLTIMO post do vídeo.

Bug que isso resolve: a regra antiga expirava o vídeo se EXISTISSE qualquer post
'publicado' há mais de N horas. Ao REPROCESSAR um vídeo já postado (ex.: para
aplicar uma correção de blur), o arquivo novo nascia com um post 'pendente' — mas
o post antigo, publicado dias atrás, continuava satisfazendo a regra e a retenção
apagava o arquivo recém-gerado na varredura seguinte (aconteceu 05/08 16:00 com
bfAcE48SKDk, ELbhBqqxMiA e m6fYG7_tGvk).

Regra nova: expira só quando o post MAIS RECENTE está 'publicado' e foi publicado
há mais de N horas. Assim, arquivo esperando postagem (pendente/postando/erro)
nunca é apagado, e o relógio das 24h reinicia a cada nova publicação.
"""

from django.db import migrations


SQL_UP = r"""
CREATE OR REPLACE FUNCTION fn_videos_expirados(p_horas integer DEFAULT 24)
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

# volta à regra anterior (qualquer post publicado há mais de N horas)
SQL_DOWN = r"""
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
        ("core", "0004_remove_video_uniq_video_por_versao_and_more"),
    ]

    operations = [
        migrations.RunPython(aplicar, reverter),
    ]
