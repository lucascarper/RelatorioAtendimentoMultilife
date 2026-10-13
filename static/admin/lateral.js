/* Lateral de navegação: recolher no desktop, abrir/fechar no celular.
   Sem eval (CSP script-src 'self'). O estado recolhido fica só neste navegador. */
(function () {
  "use strict";
  var raiz = document.documentElement;
  var CHAVE = "lateral-recolhida";

  // Aplicado antes da pintura (script síncrono no <head>) para a lateral não "pular".
  try { if (localStorage.getItem(CHAVE) === "1") raiz.classList.add("lateral-recolhida"); } catch (e) { /* sem armazenamento */ }

  document.addEventListener("DOMContentLoaded", function () {
    var botoes = document.querySelectorAll("[data-lateral-alternar]");
    var fundo = document.querySelector(".lateral-fundo");
    var movel = window.matchMedia("(max-width: 900px)");

    function rotular() {
      var aberta = movel.matches ? raiz.classList.contains("lateral-aberta") : !raiz.classList.contains("lateral-recolhida");
      botoes.forEach(function (b) {
        b.setAttribute("aria-expanded", aberta ? "true" : "false");
      });
    }

    function alternar() {
      if (movel.matches) {
        raiz.classList.toggle("lateral-aberta");
      } else {
        var recolhida = raiz.classList.toggle("lateral-recolhida");
        try { localStorage.setItem(CHAVE, recolhida ? "1" : "0"); } catch (e) { /* sem armazenamento */ }
      }
      rotular();
    }

    botoes.forEach(function (b) { b.addEventListener("click", alternar); });
    if (fundo) fundo.addEventListener("click", function () { raiz.classList.remove("lateral-aberta"); rotular(); });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && raiz.classList.contains("lateral-aberta")) { raiz.classList.remove("lateral-aberta"); rotular(); }
    });
    movel.addEventListener("change", function () { raiz.classList.remove("lateral-aberta"); rotular(); });
    rotular();
  });
})();
