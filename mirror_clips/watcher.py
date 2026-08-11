# watcher.py - Monitoramento de Performance de Shorts (Mirror-Clips)
# Verifica se os Shorts atingiram a meta de 100k views para serem copiados.

import sqlite3
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import isodate
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# Raiz do projeto (uma pasta acima de mirror_clips/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# O watcher agora só DETECTA e registra candidatos no banco (a edição é um serviço
# separado). Não importa mais o coletor.
sys.path.insert(0, str(PROJECT_ROOT))
from comum import db_bridge

# --- CONFIGURAÇÕES ---
DB_FILE = str(PROJECT_ROOT / 'data' / 'mirror_memory.db')
API_KEY = os.getenv('YOUTUBE_API_KEY', '') # Lida do .env na raiz do projeto
MIN_VIEWS = 30000   # Meta para Shorts virais
MAX_AGE_DAYS = 7    # Dá mais tempo para o Short viralizar

def obter_shorts_para_verificar(db_path):
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT video_id, published_at FROM watch_list")
    videos = cursor.fetchall()
    conn.close()
    return videos

def remover_short_da_lista(db_path, video_id):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watch_list WHERE video_id = ?", (video_id,))
    conn.commit()
    conn.close()

def verificar_performance_shorts(deve_parar=lambda: False):
    """Uma passada pela watch list. `deve_parar` é checado ENTRE vídeos: quando
    retorna True, encerra sem começar um novo vídeo (o que já está processando
    termina normalmente) — base da parada graciosa do daemon."""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando verificação de Shorts na Watch List...")

    shorts_para_verificar = obter_shorts_para_verificar(DB_FILE)
    if not shorts_para_verificar:
        print("Watch List está vazia. Nenhum Short em observação.")
        return

    try:
        youtube = build('youtube', 'v3', developerKey=API_KEY)
    except Exception as e:
        print(f"ERRO ao conectar à API do YouTube: {e}")
        return

    ids_dos_shorts = [video['video_id'] for video in shorts_para_verificar]

    try:
        # Pede em blocos de no máximo 50 se a lista for grande, mas aqui assume-se <= 50 do detector
        video_details_response = youtube.videos().list(
            part='statistics,snippet', id=",".join(ids_dos_shorts[:50])
        ).execute()
    except Exception as e:
        print(f"ERRO ao buscar detalhes dos Shorts: {e}")
        return

    itens = video_details_response.get('items', [])
    stats_map = {item['id']: item['statistics'] for item in itens}
    snippet_map = {item['id']: item.get('snippet', {}) for item in itens}

    for video in shorts_para_verificar:
        if deve_parar():
            print("🛑 Parada solicitada — encerrando após o vídeo atual (nenhum novo será iniciado).")
            return
        video_id = video['video_id']

        published_at_str = video['published_at']
        published_at = isodate.parse_datetime(published_at_str)

        # 1. Verifica se o short expirou (passou o tempo máximo de observação sem viralizar)
        if datetime.now(timezone.utc) - published_at > timedelta(days=MAX_AGE_DAYS):
            print(f"-> EXPIRADO: Short {video_id} removido por tempo.")
            remover_short_da_lista(DB_FILE, video_id)
            continue

        # 2. Verifica a contagem de visualizações
        if video_id in stats_map:
            view_count = int(stats_map[video_id].get('viewCount', 0))
            if view_count >= MIN_VIEWS:
                link = f"https://www.youtube.com/watch?v={video_id}"
                snip = snippet_map.get(video_id, {})
                # data de publicação NO CANAL (do YouTube) → só a parte da data
                pub_iso = snip.get('publishedAt') or published_at_str
                pub_data = None
                try:
                    pub_data = isodate.parse_datetime(pub_iso).date().isoformat()
                except Exception:
                    pass
                print(f"✅ VIRALIZOU! Short {video_id} atingiu {view_count} views (Meta: {MIN_VIEWS}).")
                print(f"📝 Registrando como CANDIDATO à edição no banco: {link}")
                # Registra no banco; a EDIÇÃO é feita por um serviço separado
                # (você escolhe qual editar no painel; ou AutoEditor puxa todos).
                db_bridge.registrar_candidato(
                    video_id,
                    canal_youtube_id=snip.get('channelId'),
                    canal_nome=snip.get('channelTitle', ''),
                    titulo=snip.get('title', ''),
                    url=link, views=view_count, publicado_origem=pub_data)
                remover_short_da_lista(DB_FILE, video_id)

    print("Verificação da Watch List de Shorts concluída.")


def detectar_novos_shorts():
    """
    Varre os canais cadastrados atrás de Shorts NOVOS e os põe na watch list.
    É a etapa que enche a lista que `verificar_performance_shorts` consome —
    sem ela o watcher roda em vazio para sempre.

    Roda dentro do ciclo do watcher (e não num timer próprio) para começar no
    instante em que você liga o watcher e parar quando você desliga.
    """
    import detector
    canais = db_bridge.canais_monitorados()
    if not canais:
        print("ℹ️  Nenhum canal cadastrado no painel — nada a detectar.")
        return
    detector.configurar_banco(DB_FILE)
    for canal in canais:
        try:
            detector.verificar_canal_com_api(canal)
        except Exception as e:
            print(f"⚠️  detecção falhou no canal {canal}: {e}")


def rodar_daemon(intervalo=300, deve_parar=lambda: False):
    """Loop contínuo do watcher (usado como serviço systemd). A cada ciclo faz uma
    passada; entre ciclos dorme `intervalo` s, checando `deve_parar` em pequenos
    intervalos para responder rápido a um pedido de desligamento."""
    import time
    print(f"👀 Watcher em modo daemon (intervalo {intervalo}s). Ctrl+C/stop para sair.")
    while not deve_parar():
        try:
            detectar_novos_shorts()          # 1) acha Shorts novos do canal
            verificar_performance_shorts(deve_parar)   # 2) checa quem viralizou
        except Exception as e:
            print(f"Erro no ciclo do watcher: {e}")
        for _ in range(int(intervalo)):
            if deve_parar():
                break
            time.sleep(1)
    print("👋 Watcher daemon encerrado.")


if __name__ == "__main__":
    import sys
    if "--daemon" in sys.argv:
        rodar_daemon()
    else:
        verificar_performance_shorts()
