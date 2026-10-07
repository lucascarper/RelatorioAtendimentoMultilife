# ADR 0016: Exportação de relatórios por período

- **Status:** aceita
- **Data:** 2026-10-07

## Contexto

Foi pedido exportar cada relatório (atendimento, financeiro, SESMT e atendimentos por médico) para planilha por um período escolhido, com os dados do relatório e os dados fonte usados no cálculo, mostrando o andamento numa barra que enche como água. A exportação dos atendimentos por médico fica em Configurações, separada do relatório de atendimentos.

O banco guarda o resumo calculado de cada dia, mas só o relatório de atendimentos guarda a fonte (agendamentos e mudanças de status do coletor). Títulos financeiros, documentos do SESMT e nomes de trabalhadores não ficam gravados.

## Decisão

- **Período de até 31 dias**, com a data final até hoje. Uma planilha por pedido: aba **Sobre** (período, abas e observações), abas do **relatório** (cabeçalho azul) e abas de **fonte** (prefixo "Fonte", cabeçalho verde), com cabeçalho congelado, filtro e formatos brasileiros (data, R$, h:mm:ss, %).
- **Fonte por relatório:**
  - Atendimento: agendamentos e mudanças de status gravados pelo coletor (banco, sem consultar o SGG; sem dados de pacientes).
  - Financeiro: títulos recebidos, pagos e emitidos no período, com os itens faturados, **relidos do SGG** na hora (poucas consultas).
  - SESMT: empresas, contratos, documentos e eventos do eSocial do período, **relidos do SGG** empresa por empresa (mais de mil consultas). Por isso só pode ser pedida **das 20h às 5h**, e usa a mesma trava da coleta noturna do SESMT para não somar as duas na cota da API.
  - Atendimentos por médico: os exames gravados dos médicos escolhidos, com o **nome do trabalhador lido do SGG na hora** (uma consulta por dia), nunca gravado. Exige o módulo Configurações, como a aba Médicos.
- **Em segundo plano, com andamento:** o pedido grava a exportação (tabela `exportacao`, migração 0008) e responde com a barra; a planilha é gerada depois da resposta e grava o percentual e a etapa a cada passo. A barra consulta o andamento a cada segundo (HTMX) e para quando termina. A barra é um líquido que preenche a calha da esquerda para a direita: a largura acompanha o percentual com transição suave, o corpo tem um fluxo contínuo no mesmo sentido e a frente é arredondada e ondula. O movimento para para quem pede movimento reduzido.
- **Arquivo por 24 horas:** fica no banco para download e é apagado no pedido seguinte depois desse prazo. Ao reabrir a página, a última exportação do usuário aparece (em andamento ou pronta).
- **Permissão:** pedir, acompanhar e baixar exigem o módulo do relatório (médicos: Configurações). Cada exportação aparece em Execuções.

## Consequências

- Títulos, contratos e documentos relidos do SGG mostram a situação atual, não a do dia em que o e-mail saiu; a aba Sobre avisa.
- Uma exportação do SESMT leva de 15 a 30 minutos e não roda se a coleta noturna do SESMT estiver em andamento (a tela avisa para tentar depois).
- Dias sem resumo (sem expediente) ou sem processamento dos exames aparecem na aba Sobre.
- A planilha dos atendimentos por médico tem dados pessoais de saúde: a aba de fonte e a tela avisam que é de uso interno.
