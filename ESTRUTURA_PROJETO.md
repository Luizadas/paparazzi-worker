# Estrutura do Projeto Paparazzi-Worker

O projeto é composto por **dois sistemas** que compartilham credenciais e utilitários.

```
paparazzi-worker/
├── sistema_paparazzi/     # Sistema 1 (principal, em dev): gera vídeos NOVOS/autênticos
│   ├── detector.py        # acha vídeos longos e usa a lista de influenciadores
│   ├── watcher.py         # monitora performance (≥200k views em ≤3 dias)
│   ├── coletor.py         # baixa o vídeo longo (yt-dlp) para /mnt/paparazzi
│   ├── roteirizador.py    # Whisper (transcrição) + LLM (Ollama) → JSON de cortes virais
│   ├── gerador.py         # corta o vídeo com FFmpeg → cortes_gerados/
│   └── main.py            # orquestra: roteirizador → gerador
│
├── mirror_clips/          # Sistema 2 (braço): espelha Shorts prontos e posta no TikTok
│   ├── detector.py        # acha Shorts (≤70s) de um canal fixo → watch_list
│   ├── watcher.py         # ≥30k views em ≤7 dias → dispara o coletor
│   ├── coletor.py         # baixa + espelha (hflip) + legenda (Whisper) + enfileira
│   ├── poster.py          # posta no TikTok (API oficial ou Selenium headless)
│   └── main.py            # orquestra: detector → watcher → poster
│
├── comum/                 # compartilhado entre os dois sistemas
│   ├── influenciadores/influenciadores_midia.txt
│   ├── validate_env.py    # diagnóstico de ambiente (ffmpeg, whisper, ollama)
│   └── experimentais/     # provas de conceito / legado
│       ├── Modulo1_Tiktok.py    # scraping do TikTok via Selenium
│       └── Modulo1_Twitter.py   # detecção de buzz no Twitter/X (Tweepy)
│
├── data/                  # bancos SQLite (gitignored)
│   ├── paparazzi_memory.db     # memória do Sistema 1
│   └── mirror_memory.db        # memória do Sistema 2 (criado em runtime)
│
├── .env                   # TODAS as credenciais (repo fechado → versionado)
├── .env.example           # template das variáveis
├── requirements.txt       # dependências dos dois sistemas
└── ESTRUTURA_PROJETO.md   # este arquivo
```

---

## 🎬 Sistema 1 — Paparazzi (gera vídeos novos)
*Status: em desenvolvimento (MVP na Fase 2)*

Encontra vídeos longos que viralizaram, transcreve com IA e recorta os melhores momentos.

| Etapa | Script | O que faz |
|-------|--------|-----------|
| Detecção | `detector.py` | Busca os 50 vídeos recentes do canal; ignora Shorts (≤70s); casa nomes da lista de influenciadores no título; salva o resto na `watch_list`. |
| Análise de performance | `watcher.py` | Checa views dos vídeos observados; aprova os que batem `MIN_VIEWS` (200k) dentro de `MAX_AGE_DAYS` (3). |
| Download | `coletor.py` | Baixa o vídeo longo em MP4 com `yt-dlp` para `/mnt/paparazzi`. |
| Garimpo (IA) | `roteirizador.py` | Extrai áudio (FFmpeg) → transcreve (**Whisper**) → manda pro **LLM local (Ollama)** que retorna 3–5 cortes virais em JSON. |
| Montagem | `gerador.py` | Corta o vídeo original com FFmpeg (`-c copy`) → `cortes_gerados/`. |
| Orquestração | `main.py` | Roda roteirizador → gerador. |

---

## 🪞 Sistema 2 — Mirror-Clips (espelha e posta)
*Status: em testes*

Monitora Shorts de um canal, e quando um viraliza, baixa, espelha, legenda e posta no TikTok.

| Etapa | Script | O que faz |
|-------|--------|-----------|
| Detecção | `detector.py` | Busca Shorts (≤70s) do `CHANNEL_ID` fixo e salva TODOS na `watch_list` (sem filtrar views). |
| Análise | `watcher.py` | Para cada Short observado: se atingiu **≥30k views** dentro de **≤7 dias**, dispara o coletor (bloqueante); senão, expira e remove. |
| Processamento | `coletor.py` | Baixa (yt-dlp) → espelha (FFmpeg `hflip`) → gera e queima legendas (**Whisper**) → enfileira em `fila_postagem.json`. |
| Postagem | `poster.py` | Posta no TikTok. Modo **API oficial** (preferencial) ou **Selenium** (fallback, com cookie `sessionid`, headless). Respeita `TIKTOK_PRIVACY_LEVEL`. |
| Orquestração | `main.py` | Roda detector → watcher → poster `--queue`. Flags: `--sem-detector`, `--so-poster`. |

---

## 🔑 Credenciais (`.env` na raiz)

Todas as credenciais ficam num único `.env` na raiz (lido por `load_dotenv(find_dotenv())` em qualquer script). Como o repositório é **fechado**, o `.env` é versionado — se algum dia virar público, reative as linhas `.env`/`*.env` no `.gitignore` e regenere as chaves.

| Variável | Usada por |
|----------|-----------|
| `YOUTUBE_API_KEY` | detector/watcher dos dois sistemas |
| `TWITTER_BEARER_TOKEN` | `comum/experimentais/Modulo1_Twitter.py` |
| `LLM_API_URL` | `sistema_paparazzi/roteirizador.py` |
| `TIKTOK_*` | `mirror_clips/poster.py` |

---

## ▶️ Como rodar

```bash
# Instalar dependências (FFmpeg é à parte: sudo apt install ffmpeg)
pip install -r requirements.txt

# Sistema 2 (mirror) — fluxo completo
cd mirror_clips && python main.py

# Só postar a fila já processada
cd mirror_clips && python main.py --so-poster

# Sistema 1 (paparazzi) — recorte por IA (precisa de um vídeo alvo)
cd sistema_paparazzi && python main.py

# Diagnóstico do ambiente
python comum/validate_env.py
```
