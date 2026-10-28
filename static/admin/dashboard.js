/* Dashboard do atendimento: gráfico diário (Chart.js 4.4.9, em static/admin).
   Os dados vêm no <script type="application/json"> da página (CSP script-src 'self').
   As cores saem dos tokens do CSS, então seguem o tema claro/escuro. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var tela = document.getElementById("grafico-dias");
    var fonte = document.getElementById("dados-grafico");
    if (!tela || !fonte || !window.Chart) return;  // sem gráfico: a tabela continua na página

    var dados = JSON.parse(fonte.textContent);
    var css = getComputedStyle(document.documentElement);
    function cor(nome) { return css.getPropertyValue(nome).trim(); }
    var texto = cor("--texto-2");
    var parado = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    new window.Chart(tela, {
      type: "bar",
      data: {
        labels: dados.rotulos,
        datasets: [
          { label: "Atendimentos", data: dados.atendimentos, backgroundColor: cor("--serie-atendidos"), borderRadius: 4 },
          { label: "Faltas", data: dados.faltas, backgroundColor: cor("--serie-chegadas"), borderRadius: 4 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: parado ? false : undefined,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { grid: { display: false }, ticks: { color: texto } },
          y: { beginAtZero: true, ticks: { color: texto, precision: 0 }, grid: { color: cor("--borda") } }
        },
        plugins: {
          legend: { position: "bottom", labels: { color: texto, boxWidth: 12, boxHeight: 12 } }
        }
      }
    });
  });
})();
