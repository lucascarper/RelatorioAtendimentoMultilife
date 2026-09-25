# ADR 0010: Sistema visual do admin e do monitor

- **Status:** aceita
- **Data:** 2026-09-25

## Contexto

O admin e o monitor cresceram tela a tela. A interface não tinha tema escuro, as tabelas estouravam a largura no celular, as cores de variação ficavam escritas direto no HTML e os estados usavam símbolos de texto (✓ ● ▲). Foi pedida uma revisão geral da interface, incluindo os gráficos de BI.

## Decisão

Fizemos uma evolução que preserva a marca, sem trocar a arquitetura de informação:

- **Tokens semânticos em `admin.css`**, com o mesmo nome nos dois temas: superfícies, texto, acento e estados. O tema segue o sistema (`prefers-color-scheme`).
- **Travas fixas:**
  - Um acento só, o azul da marca. O vermelho fica na faixa do topo e nos estados críticos.
  - Uma escala de raios: blocos 12px, controles 8px e etiquetas em pílula.
  - Algarismos tabulares em todos os números.
  - Nenhum travessão (— ou –) em texto visível; as faixas de turno passam a ser "06:00 às 12:59".
- **Contraste medido:** todo texto fica ≥ 4,5:1 nos dois temas.
- **Ícones Tabler** (MIT) num sprite local (`static/admin/icones.svg`), sem biblioteca em tempo de execução e compatível com a CSP `self`.
- **Gráficos:**
  - Séries com uma cor por tema. As do escuro (#E0632E e #4F8BD9 sobre #161E29) foram validadas com o validador de paleta: CVD ΔE 23,8 e contraste ≥ 3:1.
  - Marcador de "agora" no gráfico por hora e horas futuras esmaecidas.
  - O gráfico passa a ocupar a largura toda, e as tabelas de turno ficam lado a lado.
- **Movimento:** só transições curtas de estado, e tudo desliga com `prefers-reduced-motion`. A atualização a cada 5 s não esmaece mais a tela, para não piscar na TV.

## Consequências

- O e-mail continua com a paleta própria (clientes de e-mail não têm tema escuro confiável). Dele só mudaram os textos compartilhados: as faixas "às" e "-" no lugar de "—" para valor vazio.
- Uma cor nova entra primeiro como token nos dois temas. Uma série nova do gráfico entra só depois de validada com o validador de paleta.
