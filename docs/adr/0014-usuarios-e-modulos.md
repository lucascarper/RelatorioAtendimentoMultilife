# ADR 0014: Usuários, permissões e módulos do painel

- **Status:** aceita
- **Data:** 2026-10-05

## Contexto

O painel tinha um único login (o administrador das variáveis de ambiente) e um menu com oito itens soltos. Passaram a existir três relatórios (atendimentos, financeiro e SESMT), cada um com sua equipe, e foi pedido um controle de usuários com permissão por módulo, com os itens do menu reorganizados.

## Decisão

- **Cinco módulos** (`domain/acesso.py`): Atendimento, Financeiro, SESMT, Configurações e Execuções. O título do sistema passa a ser **Sistema de Relatórios**.
  - **Atendimento** reúne o painel, os destinatários (na mesma página) e dois botões para as telas de apoio: **Acompanhar AO VIVO** (monitor) e **Configurar Agendas** (unidades, agendas e guichês). Monitor e agendas mantêm os endereços `/admin/monitor` e `/admin/agendas` e ganham o botão de voltar; o menu continua marcando Atendimento nelas.
  - O painel antigo passou a `/admin/atendimento`; `/admin` leva ao primeiro módulo a que o usuário tem acesso e `/admin/destinatarios` redireciona para a seção de destinatários, para os links antigos não quebrarem.
  - **Configurações** ganha duas abas: *Regras do relatório* (o que já existia) e *Usuários*.
- **Permissão de visualização por módulo**: quem tem o módulo marcado vê e usa o módulo inteiro (não há um modo "só leitura" separado). Todas as rotas do módulo, inclusive as que gravam, passam pela mesma dependência (`exigir_modulo`). Sem permissão: página 403 "Sem acesso ao módulo X" (ou 403 seco nas requisições HTMX) e o item some do menu.
- **Usuários no banco** (tabela `usuario`, migração 0005): nome, usuário (único, minúsculas), senha com bcrypt e a lista de módulos em JSONB. Cadastro com nome, usuário e senha; usuário de 3 a 40 caracteres, senha de 8 a 72 (limite do bcrypt, que ignoraria o resto em silêncio).
- **O administrador do deploy continua fora do banco.** `ADMIN_USER` e `ADMIN_PASSWORD_HASH` valem como antes, com todos os módulos, e o nome é reservado (ninguém cadastra um usuário igual). Assim, apagar ou editar usuários nunca tranca o sistema, e o sistema continua funcionando em um banco recém-criado, sem nenhum usuário.
- **Permissões lidas do banco a cada requisição**, não copiadas para o cookie da sessão: editar ou excluir um usuário vale na próxima página. O cookie só guarda o login e o perfil (`admin` ou `usuario`); o perfil impede que, se `ADMIN_USER` mudar para o nome de um usuário cadastrado, uma sessão antiga dele vire administradora.
- **Login:** confere primeiro o administrador do deploy e depois o banco. A falha sempre gasta um bcrypt (hash fictício se o usuário não existe), então o tempo de resposta não revela quais usuários existem. O bloqueio de 5 tentativas por IP e usuário e a proteção CSRF seguem iguais.
- **Guardas do painel:** ninguém exclui o próprio usuário nem tira o próprio acesso a Configurações; não há como excluir ou editar o administrador do deploy por ali. A exclusão pede confirmação (`hx-confirm`) e recarrega a lista com a mensagem.

## Consequências

- Quem já estava logado precisa entrar de novo uma vez (a sessão agora carrega o perfil).
- Um usuário sem nenhum módulo marcado consegue entrar, mas só vê a mensagem "Nenhum módulo liberado".
- Não há recuperação de senha por e-mail: a senha é trocada por quem tem Configurações (editar o usuário) ou pelo administrador do deploy.
- Não há registro de auditoria em tela; criação, alteração e exclusão ficam nos logs estruturados (`usuario_criado`, `usuario_alterado`, `usuario_excluido`), sem senhas.
