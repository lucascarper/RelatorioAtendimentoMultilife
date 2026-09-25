/* Monitor ao vivo: atualização periódica, tela cheia e dicas do gráfico.
   Sem eval (CSP script-src 'self'); textos inseridos só com textContent. */
(function () {
  "use strict";

  var raiz = document.getElementById("monitor");
  if (!raiz) return;

  // ------------------------------------------------------------ atualização
  // Só busca com a aba visível: um monitor esquecido em segundo plano não gera carga.
  var intervalo = (parseInt(raiz.getAttribute("data-intervalo"), 10) || 30) * 1000;
  var ultima = Date.now();
  var abertos = {};

  var buscando = false;

  function atualizar() {
    // Uma busca por vez: se a rede estiver lenta, a próxima espera a anterior terminar.
    if (document.hidden || !window.htmx || buscando) return;
    buscando = true;
    ultima = Date.now();
    window.htmx.ajax("GET", raiz.getAttribute("data-url"), {
      source: raiz,
      target: raiz,
      swap: "innerHTML",
    });
  }

  window.setInterval(function () {
    if (Date.now() - ultima >= intervalo - 250) atualizar();
  }, 1000);

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && Date.now() - ultima >= intervalo) atualizar();
  });

  // A troca do conteúdo não fecha a "tabela" que a pessoa abriu.
  raiz.addEventListener("htmx:beforeSwap", function () {
    abertos = {};
    raiz.querySelectorAll("details[id]").forEach(function (d) { abertos[d.id] = d.open; });
    raiz.setAttribute("aria-busy", "true");
    esconder();
  });
  raiz.addEventListener("htmx:afterSwap", function () {
    Object.keys(abertos).forEach(function (id) {
      var d = document.getElementById(id);
      if (d) d.open = abertos[id];
    });
    raiz.setAttribute("aria-busy", "false");
  });
  function liberar() {
    buscando = false;
    raiz.setAttribute("aria-busy", "false");
  }
  raiz.addEventListener("htmx:afterRequest", liberar);
  raiz.addEventListener("htmx:responseError", liberar);
  raiz.addEventListener("htmx:sendError", liberar);

  // ------------------------------------------------------------ tela cheia
  var botao = document.querySelector("[data-tela-cheia]");
  if (botao && document.documentElement.requestFullscreen) {
    botao.addEventListener("click", function () {
      if (document.fullscreenElement) document.exitFullscreen();
      else document.documentElement.requestFullscreen();
    });
    document.addEventListener("fullscreenchange", function () {
      var ativo = Boolean(document.fullscreenElement);
      botao.setAttribute("aria-pressed", String(ativo));
      var texto = botao.querySelector("span");
      var uso = botao.querySelector("use");
      if (texto) texto.textContent = ativo ? "Sair da tela cheia" : "Tela cheia";
      if (uso) uso.setAttribute("href", "/static/admin/icones.svg#i-" + (ativo ? "sair-tela-cheia" : "tela-cheia"));
    });
  } else if (botao) {
    botao.hidden = true;
  }

  // ------------------------------------------------------------ dicas do gráfico
  var dica = document.createElement("div");
  dica.className = "dica";
  dica.setAttribute("role", "tooltip");
  dica.hidden = true;
  document.body.appendChild(dica);
  var alvoAtual = null;

  function preencher(alvo) {
    var dados;
    try { dados = JSON.parse(alvo.getAttribute("data-dica")); } catch (e) { return false; }
    dica.replaceChildren();
    var titulo = document.createElement("div");
    titulo.className = "dica-titulo";
    titulo.textContent = dados.titulo;
    dica.appendChild(titulo);
    (dados.linhas || []).forEach(function (linha) {
      var item = document.createElement("div");
      item.className = "dica-linha";
      var chave = document.createElement("span");
      chave.className = "dica-chave serie-" + linha[0];
      var valor = document.createElement("strong");
      valor.textContent = linha[2];
      var rotulo = document.createElement("span");
      rotulo.textContent = linha[1];
      item.append(chave, valor, rotulo);
      dica.appendChild(item);
    });
    return true;
  }

  function posicionar(x, y) {
    var margem = 12;
    var largura = dica.offsetWidth;
    var altura = dica.offsetHeight;
    var esquerda = Math.min(x + margem, window.innerWidth - largura - margem);
    var topo = y - altura - margem;
    if (topo < margem) topo = y + margem;
    dica.style.left = Math.max(margem, esquerda) + "px";
    dica.style.top = topo + "px";
  }

  function mostrar(alvo, x, y) {
    if (alvo !== alvoAtual) {
      if (!preencher(alvo)) return;
      alvoAtual = alvo;
    }
    dica.hidden = false;
    posicionar(x, y);
  }

  function esconder() {
    dica.hidden = true;
    alvoAtual = null;
  }

  raiz.addEventListener("pointermove", function (e) {
    var alvo = e.target.closest ? e.target.closest("[data-dica]") : null;
    if (alvo) mostrar(alvo, e.clientX, e.clientY);
    else esconder();
  });
  raiz.addEventListener("pointerleave", esconder);
  raiz.addEventListener("focusin", function (e) {
    var alvo = e.target.closest ? e.target.closest("[data-dica]") : null;
    if (!alvo) return esconder();
    var caixa = alvo.getBoundingClientRect();
    mostrar(alvo, caixa.left + caixa.width / 2, caixa.top + caixa.height / 3);
  });
  raiz.addEventListener("focusout", esconder);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") esconder(); });
  window.addEventListener("scroll", esconder, { passive: true });
})();
