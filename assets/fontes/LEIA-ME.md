# Fontes da legenda queimada

`Anton.ttf` — usada na legenda karaokê (uma palavra por vez), para reproduzir a
tipografia do canal original: condensada pesada, caixa alta. Licença SIL OFL 1.1
(uso livre, inclusive comercial). Origem: https://fonts.google.com/specimen/Anton

A fonte NÃO é instalada no sistema: o filtro `ass` do ffmpeg recebe
`fontsdir=<repo>/assets/fontes`, então ela viaja com o projeto.

Calibração medida (libass renderiza a Anton a ~0,50 do fontsize):
  fontsize = 0,0963 × altura do vídeo  → altura de letra 4,79% de H
  ScaleX 110 → largura idêntica à do original (425 px vs 424 px em "TUDO" em 4K)
