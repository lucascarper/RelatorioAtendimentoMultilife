# ADR 0004 — E-mail via SMTP da KingHost, HTML com CSS inline e logo por CID

- **Status:** aceita (depende do spike de saída SMTP na Railway)
- **Data:** 2026-09-24

## Contexto

Decisão do cliente: usar a conta de e-mail da KingHost que a MultiLife já usa, via SMTP. O relatório precisa ser lido em Outlook e Gmail, no computador e no celular, com a identidade visual da marca.

## Decisão

- `smtplib` com STARTTLS na porta 587, ou SSL direto na 465 (`SMTP_SSL` força um dos modos). As falhas viram `ErroEmail`, e a mensagem nunca contém a senha.
- Mensagem `multipart/alternative`: texto puro e HTML. A logo vai como `multipart/related` (Content-ID), porque imagens remotas são bloqueadas por padrão no Outlook e `data:` URI não funciona no Gmail.
- Layout em tabelas, CSS inlined pelo premailer (as media queries de celular ficam no `<style>`), `color-scheme: light only`.
- Idempotência: `resumo_diario.status_envio` é reservado com `UPDATE … WHERE status IN ('pendente','falha')`, que é atômico. Reexecutar não duplica e-mails; reenviar exige `forcar`.
- `EMAIL_BACKEND=arquivo` grava `.eml` e `.html` localmente (desenvolvimento, sem envio real).

## Consequências e plano B

Se a Railway bloquear a saída SMTP no plano usado, as opções são um relay HTTP→SMTP ou um provedor transacional por HTTP. Basta implementar outro `EnviadorEmail` (porta da aplicação), sem mudar os casos de uso.
