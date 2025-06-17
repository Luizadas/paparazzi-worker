# --- paparazzi-worker: Módulo 1 (Detecção) ---
# Versão 0.7: Monitor com "Batching" de Palavras-Chave

import tweepy
import time
from datetime import datetime, timezone
import math

# --- 1. CONFIGURAÇÃO ---
BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAABKK2QEAAAAAQZeWvqeP1Ji73OoVHh84rf2zhxM%3DX9MzWoEC98p2gk1nUU37pOcUlZifqcJpIRfhJTZigvQoseSdA3"

LISTA_DE_ALVOS = ["whindersson", "felipeneto", "Virginia", "g1"]
LISTA_DE_PALAVRAS_CHAVE = [
    "treta", "polêmica", "cancelado", "processo",
    "namorando", "separou", "traição", "gravida"
]

INTERVALO_EM_MINUTOS = 15
PERCENTUAL_DE_ALERTA = 0.0005
# NOVO PARÂMETRO: Define o tamanho de cada lote de palavras-chave
TAMANHO_DO_LOTE = 3


# --- 2. FUNÇÃO AUXILIAR PARA DIVIDIR A LISTA ---
def dividir_em_lotes(lista, tamanho_lote):
    """Divide uma lista em lotes menores de um tamanho específico."""
    for i in range(0, len(lista), tamanho_lote):
        yield lista[i:i + tamanho_lote]


# --- 3. LÓGICA DO MONITOR ---
def monitorar_com_batching():
    print(f"✅ Monitor de Polêmicas v0.7 (Batching) iniciado. Tamanho do lote: {TAMANHO_DO_LOTE}")
    client = tweepy.Client(BEARER_TOKEN)

    # Divide a lista de palavras-chave em lotes UMA SÓ VEZ
    lotes_de_palavras = list(dividir_em_lotes(LISTA_DE_PALAVRAS_CHAVE, TAMANHO_DO_LOTE))
    print(f"Palavras-chave divididas em {len(lotes_de_palavras)} lotes.")

    while True:
        print("-" * 70)
        agora = datetime.now(timezone.utc)
        print(f"[{agora.strftime('%Y-%m-%d %H:%M:%S')}] Iniciando novo ciclo...")

        for influenciador in LISTA_DE_ALVOS:
            contagem_acumulada = 0
            try:
                print(f"\nVerificando @{influenciador}...")
                user_response = client.get_user(username=influenciador, user_fields=["public_metrics"])
                if user_response.data is None: continue

                num_seguidores = user_response.data.public_metrics['followers_count']
                limite_dinamico = math.ceil(num_seguidores * PERCENTUAL_DE_ALERTA)
                print(f"  -> Limite dinâmico: {limite_dinamico} menções.")

                # Loop através dos LOTES de palavras, não da lista inteira
                for i, lote in enumerate(lotes_de_palavras):
                    or_clause = " OR ".join([f'"{palavra}"' for palavra in lote])
                    query_keywords_part = f"({or_clause})"
                    query = f'"{influenciador}" {query_keywords_part} -is:retweet'

                    print(f"  -> Buscando Lote {i + 1}/{len(lotes_de_palavras)}: {lote}")

                    response = client.get_recent_tweets_count(query=query)
                    contagem_do_lote = response.meta['total_tweet_count']
                    contagem_acumulada += contagem_do_lote

                    print(f"     -> Resultado do lote: {contagem_do_lote} tweets.")
                    # Pausa CRÍTICA para dar um respiro à API entre as buscas complexas
                    time.sleep(5)

                print(f"  -> Volume total de buzz acumulado: {contagem_acumulada} tweets.")

                if contagem_acumulada > limite_dinamico:
                    print("\n🔥🔥🔥 ALERTA DE TENDÊNCIA! NÍVEL ALTO! 🔥🔥🔥")
                    print(f"Assunto: Alta atividade para '{influenciador}'.")
                    print(f"Volume: {contagem_acumulada} tweets (Limite era {limite_dinamico}).")

            except Exception as e:
                print(f"  -> ❌ AVISO: Ocorreu um erro ao processar @{influenciador}. Continuando...")
                print(f"     Detalhe do erro: {e}")
                if "429" in str(e):
                    print("     -> Rate Limit. Saindo do loop deste influenciador para o próximo ciclo.")
                    break

        print(f"\n🏁 Ciclo de verificação completo. Próximo ciclo em {INTERVALO_EM_MINUTOS} minutos.")
        time.sleep(INTERVALO_EM_MINUTOS * 60)


# --- 4. EXECUÇÃO ---
if __name__ == "__main__":
    if BEARER_TOKEN == "SEU_BEARER_TOKEN_AQUI":
        print("🛑 ATENÇÃO: Você precisa configurar seu BEARER_TOKEN no código antes de rodar.")
    else:
        monitorar_com_batching()