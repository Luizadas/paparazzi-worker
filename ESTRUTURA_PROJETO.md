# Estrutura e Funções do Projeto Paparazzi-Worker

Este documento descreve as fases de desenvolvimento do projeto e detalha qual script é responsável por cada etapa do processo de automação.

---

## 🟢 Fase 1: Detecção e Coleta (Infraestrutura)
*Status: Concluída*

Nesta fase, o sistema foca em encontrar conteúdo relevante e baixá-lo para processamento.

### 📝 [detector.py](file:///home/luiz/github/paparazzi-worker/Scripts/detector.py) (O Vigia)
**Função:** Monitorar canais do YouTube em tempo real para identificar novos vídeos.
- `verificar_canal_com_api()`: Busca os 50 vídeos mais recentes de um canal.
- `filtragem (Shorts)`: Ignora vídeos com menos de 70 segundos.
- `classificação`: Se um influencer da lista `influenciadores_midia.txt` for detectado no título, emite alerta de download imediato.
- `registro`: Se não for imediato, salva o vídeo no banco `watch_list` para observação de performance.

### 📝 [watcher.py](file:///home/luiz/github/paparazzi-worker/Scripts/watcher.py) (O Analista)
**Função:** Monitorar a performance de vídeos que foram "colocados em observação" pelo detector.
- `obter_videos_para_verificar()`: Lê a lista de observação do banco de dados.
- `verificar_performance_videos()`: Checa com a API do YouTube se o vídeo atingiu o critério de viralização (ex: > 200.000 views em menos de 3 dias).
- `limpeza`: Remove da lista vídeos que expiraram (ficaram velhos sem atingir as métricas).

### 📝 [coletor.py](file:///home/luiz/github/paparazzi-worker/Scripts/coletor.py) (O Baixador)
**Função:** Realizar o download físico dos vídeos aprovados.
- `baixar_video(url)`: Utiliza o `yt-dlp` para extrair o vídeo na melhor qualidade MP4 disponível e salvá-lo localmente (diretório `/mnt/paparazzi/`).

---

## 🔵 Fase 2: O Cérebro da Operação (Inteligência Artificial)
*Status: Foco Atual (MVP)*

Nesta fase, o sistema analisa o vídeo longo para extrair os melhores momentos usando IA.

### 📝 [roteirizador.py](file:///home/luiz/github/paparazzi-worker/Scripts/roteirizador.py) (O Garimpeiro)
**Função:** Transformar um vídeo longo em um roteiro de cortes via análise de áudio e texto.
- `transcrever_video_localmente()`: Usa FFmpeg para extrair o som e o modelo **OpenAI Whisper (Local)** para transcrever tudo o que foi dito com marcação de tempo (timestamps).
- `analisar_transcricao_com_llm()`: Envia o texto para um modelo de linguagem (ex: **DeepSeek**) via API local (Ollama) com um prompt especializado em viralização.
- **Saída:** Gera um JSON contendo o tempo de início, fim e a justificativa para 3 a 5 "clipes virais".

---

## 🟠 Fase 3: Produção e Automação (Geração do Corte)
*Status: Desenvolvimento / Backlog*

Nesta fase, o sistema executa o corte físico e orquestra o fluxo completo.

### 📝 [gerador.py](file:///home/luiz/github/paparazzi-worker/Scripts/gerador.py) (A Linha de Montagem)
**Função:** Executar as ordens de corte geradas pelo roteirizador.
- `gerar_cortes_do_roteiro(video, roteiro)`: Recebe o vídeo original e o JSON de cortes.
- `execução FFmpeg`: Usa comandos de sistema para extrair as partes exatas do vídeo original de forma eficiente (sem perda de qualidade via `-c copy`).
- **Saída:** Arquivos MP4 individuais salvos na pasta `cortes_gerados/`.

### 📝 [main.py](file:///home/luiz/github/paparazzi-worker/Scripts/main.py) (O Maestro)
**Função:** Orquestrar a execução sequencial dos módulos.
- `executar_script()`: Gerencia chamadas de subprocessos e verifica erros.
- **Fluxo Principal:** Verifica se o vídeo existe -> Chama o Roteirizador -> Se bem-sucedido, chama o Gerador.

---

## 🛠️ Scripts Experimentais / Legados
- **[Modulo1_Tiktok.py](file:///home/luiz/github/paparazzi-worker/Scripts/Modulo1_Tiktok.py)**: Teste de detecção no TikTok usando Selenium (Web Scraping).
- **[Modulo1_Twitter.py](file:///home/luiz/github/paparazzi-worker/Scripts/Modulo1_Twitter.py)**: Teste de detecção de tendências (Buzz) no Twitter usando a API Tweepy.
