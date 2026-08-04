"""
Geração da LEGENDA do post (caption) via LLM local (Ollama).

Módulo COMPARTILHADO: usado pelo watcher/coletor (gera a legenda durante o
processamento, antes de postar) e pelo painel (botão "regerar" na aba de vídeos).
Mantém a legenda como fonte única de lógica, para os dois lados gerarem igual.
"""

import os
import re

import requests
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:11434/api/generate")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:3b")


def gerar_legenda_ia(texto_transcricao: str, titulo_original: str = "") -> str:
    """
    Gera a legenda do TikTok via LLM local (Ollama) com BASE no conteúdo do
    próprio vídeo (transcrição do Whisper + título original), orientando o LLM
    com técnicas de viralização no TikTok.
    Retorna string vazia se o LLM falhar (o poster cai no template padrão).
    """
    texto = (texto_transcricao or "").strip()
    if not texto:
        return ""

    contexto_titulo = f"\nTÍTULO ORIGINAL DO VÍDEO: {titulo_original}\n" if titulo_original else ""

    # Prompt baseado em boas práticas de legenda viral no TikTok (2026):
    # gancho curto na 1ª frase (o que aparece antes do "ver mais"), CTA para
    # gerar comentários, 1-2 palavras-chave (SEO da busca) e 3-5 hashtags
    # (amplas + de nicho). Legenda curtíssima e escaneável.
    prompt = f"""Você é um especialista em viralização no TikTok no Brasil. Escreva a LEGENDA (descrição) de um post curto.

A transcrição automática abaixo pode estar IMPERFEITA. NÃO copie nem resuma a transcrição — apenas ENTENDA o TEMA e escreva uma legenda NOVA e original.

Estrutura da legenda (siga à risca):
1) GANCHO forte e CURTO na primeira frase (ideal 20-60 caracteres): curiosidade, opinião polêmica, choque ou promessa de valor. É o que aparece antes do "ver mais".
2) (opcional) uma CTA curta pra gerar comentários: "comenta aí", "concorda?", "marca alguém".
3) 3 a 5 HASHTAGS no fim: misture amplas (#fyp #viral #foryou) com 1-2 específicas do tema.

Regras:
- Curtíssima e escaneável: fora as hashtags, no máximo ~150 caracteres.
- Português do Brasil, tom coloquial e natural. No máximo 1-2 emojis.
- Inclua 1-2 palavras-chave do tema (ajuda na busca do TikTok).
- PROIBIDO: copiar/resumir a transcrição, textão, caracteres de outro idioma (chinês etc.), aspas, "Legenda:".
- Responda SÓ com a legenda final (gancho + hashtags), em UMA linha.
{contexto_titulo}
--- TRANSCRIÇÃO (pode estar imperfeita) ---
{texto[:2000]}
"""

    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.8, "num_predict": 120},
    }
    try:
        print("🧠 Gerando legenda com IA (LLM)...")
        resp = requests.post(LLM_API_URL, json=payload, timeout=180)
        resp.raise_for_status()
        saida = resp.json().get("response", "") or ""

        legenda = _pos_processar_legenda(_sanitizar_legenda(saida))
        if legenda:
            print(f"✅ Legenda IA: {legenda}")
        return legenda[:2200]
    except Exception as e:
        print(f"⚠️ Falha ao gerar legenda com IA: {e}. Usarei o template padrão.")
        return ""


def _sanitizar_legenda(saida: str) -> str:
    """Limpa a resposta do LLM: remove blocos de raciocínio, caracteres CJK/estrangeiros
    e prefixos, deixando apenas a legenda em português + hashtags."""
    if not saida:
        return ""
    # Remove blocos <think>...</think> de modelos de raciocínio
    saida = re.sub(r"<think>.*?</think>", "", saida, flags=re.DOTALL)
    # Remove caracteres CJK (chinês/japonês/coreano) e formas de largura total
    saida = re.sub(
        r"[　-〿぀-ヿㇰ-ㇿ㐀-䶿"
        r"一-鿿ꥠ-꥿가-퟿豈-﫿＀-￯]",
        "", saida,
    )
    # Junta em uma linha, remove aspas e prefixos comuns
    linhas = [l.strip().strip('"').strip() for l in saida.splitlines() if l.strip()]
    legenda = " ".join(linhas).strip()
    legenda = re.sub(r"^(legenda|caption)\s*:\s*", "", legenda, flags=re.IGNORECASE)
    # Colapsa espaços múltiplos criados pela remoção de caracteres
    legenda = re.sub(r"\s{2,}", " ", legenda).strip()
    return legenda


def _pos_processar_legenda(legenda: str) -> str:
    """Aplica as boas práticas de legenda viral de forma determinística:
    - separa gancho (texto) das hashtags;
    - encurta o gancho (~150 chars, sem cortar no meio da palavra);
    - garante de 3 a 5 hashtags (amplas + de nicho), sem duplicar.
    """
    if not legenda:
        return ""
    tokens = legenda.split()
    hashtags, texto_toks = [], []
    for t in tokens:
        (hashtags if t.startswith("#") and len(t) > 1 else texto_toks).append(t)
    texto = " ".join(texto_toks).strip(" -–—:")

    # Gancho curto (best practice: primeira linha curtíssima)
    if len(texto) > 150:
        texto = texto[:150].rsplit(" ", 1)[0].rstrip(",;.") + "…"

    # Dedup de hashtags preservando ordem
    vistos, hs = set(), []
    for h in hashtags:
        hl = h.lower()
        if hl not in vistos:
            vistos.add(hl); hs.append(h)
    # Garante hashtags amplas e limita a 5 (3-5 é o recomendado)
    for base in ("#fyp", "#viral", "#foryou"):
        if len(hs) >= 5:
            break
        if base not in vistos:
            vistos.add(base); hs.append(base)
    hs = hs[:5]

    return (f"{texto} {' '.join(hs)}").strip()
