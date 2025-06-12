# --- paparazzi-worker: Módulo 1 (Detecção) ---
# Versão 0.1: Script de Conexão e Coleta Inicial

# Para instalar a biblioteca necessária, abra seu terminal e digite:
# pip install tweepy

import tweepy

# --- 1. CONFIGURAÇÃO DAS CREDENCIAIS (SUA AÇÃO AQUI) ---
# Cole aqui o "Bearer Token" que você obteve no portal de desenvolvedor do Twitter.
# É a forma mais simples e segura para fazer requisições de "leitura" (como a nossa).
BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAABKK2QEAAAAAQZeWvqeP1Ji73OoVHh84rf2zhxM%3DX9MzWoEC98p2gk1nUU37pOcUlZifqcJpIRfhJTZigvQoseSdA3"

# --- 2. DEFINIÇÃO DO ALVO ---
# Vamos usar um influenciador como nosso primeiro alvo para o MVP.
# Mude este nome de usuário para quem você quiser monitorar.
INFLUENCIADOR_ALVO_USERNAME = "mauro_davi6"  # Exemplo: whindersson, felipe_neto (sem o @)


# --- 3. LÓGICA DO SCRIPT ---

# Função principal que executa a busca
def buscar_tweets_recentes(token, username):
    """
    Conecta na API do Twitter v2 e busca os tweets mais recentes de um usuário.
    """
    print(f"✅ Iniciando conexão com a API do Twitter...")

    try:
        # Autenticando com o Bearer Token. É como mostrar o crachá para entrar.
        client = tweepy.Client(token)

        print(f"🎯 Buscando ID do usuário para '{username}'...")

        # A API prefere trabalhar com IDs numéricos em vez de nomes de usuário.
        # Então, primeiro pegamos o ID do usuário.
        user_response = client.get_user(username=username)
        if user_response.data is None:
            print(f"❌ ERRO: Usuário '{username}' não encontrado.")
            return

        user_id = user_response.data.id
        print(f"✔️ ID encontrado: {user_id}")

        print(f"🔍 Buscando os 10 tweets mais recentes de @{username}...")

        # Agora, buscamos os tweets (a "timeline") do usuário usando o ID dele.
        tweets_response = client.get_users_tweets(id=user_id, max_results=10)

        if not tweets_response.data:
            print(f"😕 Nenhum tweet encontrado para @{username} recentemente.")
            return

        print("-" * 50)
        print(f"ÚLTIMOS TWEETS DE @{username}:")

        # Itera sobre cada tweet encontrado e imprime o texto.
        for tweet in tweets_response.data:
            print(f"\n-[Tweet ID: {tweet.id}]")
            print(f"   {tweet.text}")
            print("-" * 20)

    except Exception as e:
        print(f"❌ Ocorreu um erro geral: {e}")
        print("\nVerifique se o seu 'Bearer Token' está correto e se o nome do usuário existe.")


# --- 4. EXECUÇÃO ---
if __name__ == "__main__":
    if BEARER_TOKEN == "COLE_SEU_BEARER_TOKEN_AQUI":
        print("🛑 ATENÇÃO: Você precisa configurar seu BEARER_TOKEN no código antes de rodar.")
    else:
        buscar_tweets_recentes(BEARER_TOKEN, INFLUENCIADOR_ALVO_USERNAME)