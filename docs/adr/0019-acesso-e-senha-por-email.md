# ADR 0019: Enviar acesso e "Esqueci minha senha" por e-mail

- **Status:** aceita
- **Data:** 2026-10-29

## Contexto

Pela [ADR 0014](0014-usuarios-e-modulos.md), a senha de um usuário do painel só era trocada por quem tem Configurações, e não havia recuperação por e-mail. Foram pedidas duas funções:

1. Na tela de usuários, mandar ao e-mail do usuário o login e o acesso.
2. Na tela de login, um "Esqueci minha senha" que manda um link para cadastrar a nova senha.

As senhas são guardadas só com bcrypt, então a senha atual não pode ser enviada. O cadastro também não tinha e-mail.

## Decisão

- **E-mail no cadastro** (migração 0011): campo opcional, único entre os usuários e guardado em minúsculas. Os usuários atuais ficam sem e-mail até alguém editar. Sem e-mail, não dá para enviar o acesso nem recuperar a senha. O administrador do deploy (`ADMIN_USER`) continua fora do banco e fora deste fluxo.
- **Um link, não uma senha.** As duas funções mandam o mesmo e-mail: o login do usuário e um link para `/redefinir-senha/{token}`, onde ele cadastra a senha (com confirmação). Nenhuma senha passa pelo e-mail, e a senha atual continua valendo até o link ser usado. Foi a opção escolhida em vez de gerar uma senha provisória.
- **Token:** `secrets.token_urlsafe(32)` (256 bits). A tabela `token_senha` guarda só o SHA-256 dele. Basta um hash rápido, porque o token é aleatório e longo e não dá para descobri-lo por força bruta. Com o banco vazado, os links não podem ser usados.
- **Uso único e validade:** o link do "Esqueci minha senha" vale **1 hora**. O do "Enviar acesso" vale **24 horas**, porque quem recebe um acesso novo pode demorar a ver o e-mail. Ao definir a senha, o token é apagado com `DELETE … RETURNING`, então dois envios do mesmo link não passam os dois, e os outros links do mesmo usuário também caem. Excluir o usuário apaga os links dele (`ON DELETE CASCADE`). Os vencidos são apagados a cada link novo, pela hora da aplicação.
- **Trocar a senha pelo link derruba as sessões abertas** com a senha antiga (a impressão da senha da ADR 0014). O bloqueio de login por tentativas **não** é liberado por esse caminho.
- **Onde fica a regra:** o token (geração, hash, validade, uso único) está em `application/recuperacao_senha.py`, atrás das portas `tokens_senha` e `RenderizadorEmail.acesso`. As rotas só montam o endereço do link.
- **E-mail sem perfil:** a página diz "Não existe perfil cadastrado com esse e-mail.", a pedido do negócio. **Risco aceito:** isso revela a quem tentar quais e-mails têm cadastro. O limite de pedidos reduz essa varredura, mas não a impede. Para fechar o risco, basta trocar a mensagem por uma resposta única ("se existir um perfil, enviamos o link").
- **Limite de pedidos** (o mesmo `ControleTentativas` do login, 5 a cada 15 minutos, em memória e com 1 réplica do serviço web). Passou disso, a página responde 429. São dois limites:
  - **Por IP:** só conta pedido com e-mail inválido ou sem perfil, que é o que serve para varrer cadastros. Colegas atrás do mesmo IP (rede da empresa) pedem o seu link sem gastar o limite uns dos outros.
  - **Por e-mail:** conta cada envio, para ninguém lotar a caixa de outra pessoa. A chave só é criada para e-mails com perfil, então a memória não cresce com e-mails inventados.
  - **Limite conhecido:** o uvicorn roda com `--forwarded-allow-ips='*'` e usa o IP de `X-Forwarded-For`, que o cliente consegue forjar. Quem faz isso contorna o limite por IP e pode varrer e-mails com perfil. O limite por e-mail continua valendo. Fechar de vez exige confiar só no IP que o proxy da Railway acrescenta.
- **Endereço do link:** usa `ADMIN_URL` quando está definido (só o domínio conta, o caminho `/admin` é trocado). Sem ele, o link usa o endereço da própria requisição, que o cliente controla (cabeçalho `Host`). Em produção, `ADMIN_URL` deve estar definido.
- **Envio síncrono** pelo `EnviadorEmail` do container (SMTP da KingHost ou arquivo em desenvolvimento). Se o envio falhar, a tela avisa e o link criado só vence.

## Consequências

- Para usar as duas funções, os usuários atuais precisam ter o e-mail cadastrado (Configurações → Usuários → Editar).
- O botão **Enviar acesso** só aparece para quem tem e-mail. Ele pede confirmação e pode ser usado de novo (cada envio gera um link novo).
- Logs estruturados: `esqueci_senha_link_enviado`, `esqueci_senha_bloqueado`, `esqueci_senha_falha_email`, `acesso_enviado`, `acesso_falha_email` e `senha_redefinida`, sem token e sem senha.
- A tela de recuperação pode ser usada para descobrir quais e-mails têm cadastro (ver o risco acima).
