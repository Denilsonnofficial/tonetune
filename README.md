# Tonetune

<p align="center">
  <img src="docs/logo.svg" width="144" alt="Logo do Tonetune">
</p>

<p align="center">
  <b>Reprodutor de ritmos AC7 do Casio CT-X800</b><br>
  Ouça e teste seus ritmos no PC sem precisar ir ao teclado.
</p>

<p align="center">
  <img src="docs/screenshot.png" alt="Tonetune tocando a Variação 1 de um ritmo"
       width="900">
</p>

## O que é

O **Tonetune** é um reprodutor leve para arquivos `.AC7` do Casio CT-X800
(e da família CT-X). Ele abre o ritmo, mostra as seções e as oito partes e
toca cada seção em loop com um SoundFont GM.

Ele foi criado a partir do
[ctx800-editor](https://github.com/Denilsonnofficial/ctx800-editor), mas é uma
ferramenta separada e **somente de reprodução**: não edita, não salva e não
exporta AC7. O fluxo é:

1. editar o ritmo no editor (ou por script);
2. abrir o `.AC7` resultante no Tonetune;
3. escolher a seção e ouvir;
4. quando o arquivo for regravado, o Tonetune **recarrega sozinho**.

Assim dá para testar uma alteração de bateria, baixo, strings ou volume sem
levar o pendrive ao teclado a cada tentativa.

## Recursos

- Abre arquivos `.AC7` com 6 ou 12 elementos.
- Lista **Intro 1/2, Variações 1–4, Fills 1–4 e Endings 1/2**, mostrando
  compassos, notas e uma miniatura de atividade por compasso.
- **Oito partes** com instrumento, volume do AC7, mute (**M**) e solo (**S**).
- Reprodução em loop com **FluidSynth + GStreamer**.
- Controle de **andamento** para testar o ritmo mais devagar ou mais rápido
  (não altera o arquivo).
- Volume geral independente do volume gravado no ritmo.
- Linha do tempo colorida com as notas de todas as partes e playhead.
- **Recarregamento automático** quando o arquivo é salvo por outro programa.
- Abrir por botão, arrastar e soltar, arquivos recentes ou linha de comando:
  `tonetune MEU_RITMO.AC7`.
- Atalhos de teclado para trocar de seção e tocar sem parar.
- Ícone SVG próprio, integrado ao menu de aplicativos do Lig OS/GNOME.
- Tema claro e escuro do sistema.

## Requisitos

Testado no Debian 13 (Lig OS), com GTK 4, Libadwaita, GStreamer, FluidSynth e
Python 3.

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
    fluidsynth fluid-soundfont-gm python3-mido
```

Se preferir instalar o `mido` pelo pip:

```bash
python3 -m pip install --user -r requirements.txt
```

## Instalar

```bash
git clone https://github.com/Denilsonnofficial/tonetune.git
cd tonetune
./install.sh
```

O `install.sh` cria o atalho `tonetune` em `~/.local/bin`, registra o aplicativo
no menu, instala o ícone SVG e associa arquivos `.AC7` ao Tonetune.

> O instalador também remove o atalho antigo do **Editor de Ritmos CT-X800**,
> se ele existir. O código-fonte em `~/ctx800` e o repositório do GitHub não
> são alterados.

Depois é só procurar por **Tonetune** no menu ou rodar:

```bash
tonetune
tonetune /caminho/DO_RITMO.AC7
```

Para rodar sem instalar:

```bash
./run.sh
# ou
python3 main.py
```

## Como usar

1. Abra um `.AC7` pelo botão **Abrir ritmo**, pelo menu ou arrastando o arquivo
   para a janela.
2. A **Variação 1** é selecionada automaticamente quando existe.
3. Aperte **Espaço** ou o botão de play para ouvir a seção em loop.
4. Clique em outra seção (ou use as teclas **1–9**) para trocar na hora.
5. Use **M** para mutar e **S** para isolar uma parte.
6. Ajuste o **BPM** ou o volume geral se quiser testar em outra condição.
7. Salve o ritmo de novo no editor: o Tonetune recarrega e reinicia a seção
   automaticamente se estiver tocando.

### Atalhos

| Ação | Atalho |
|---|---|
| Tocar / pausar | Espaço |
| Parar | Esc |
| Repetir seção | L |
| Tocar a seção 1–9 (e 0 para a 10) | 1–9, 0 |
| Recarregar arquivo | F5 |
| Abrir outro ritmo | Ctrl+O |
| Mutar / isolar parte | botões M / S |

## O que é fiel e o que é aproximado

O Tonetune usa os patches, volumes, pans, reverbs e choruses gravados no AC7.
A prévia no PC usa um **SoundFont GM**, então:

- o programa GM equivalente é usado para cada timbre;
- as duas partes de bateria são misturadas em um único kit GM;
- o resultado é uma boa aproximação para ensaiar e comparar versões, mas o
  timbre definitivo só sai no CT-X800.

O aplicativo **não aplica chord sync** (transposição por acorde): as notas são
tocadas como estão gravadas, em Dó. É o ideal para conferir a programação do
ritmo.

## Onde ficam os arquivos

```
~/.cache/tonetune/                 WAVs renderizados (cache)
~/.config/tonetune/config.json     recentes, volume, última pasta
```

Nenhum arquivo do ritmo é modificado.

## Limitações

- Somente leitura: não edita nem exporta AC7.
- Não importa MIDI nem styles Yamaha `.STY`.
- Não grava no pendrive nem no teclado.
- A prévia é GM, não os timbres exatos do CT-X800.
- Elementos vazios aparecem desabilitados na lista de seções.

## Créditos

- [ctx800-editor](https://github.com/Denilsonnofficial/ctx800-editor) — editor
  de ritmos que originou este projeto.
- [ac7maker](https://github.com/michgz/ac7maker) — documentação e ferramentas
  do formato AC7.
- GTK 4, Libadwaita, GStreamer, FluidSynth e mido.

## Licença

Distribuído sob a **GPL-3.0**. Veja [LICENSE](LICENSE).
