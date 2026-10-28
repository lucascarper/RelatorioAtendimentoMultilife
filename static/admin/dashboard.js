/* Dashboard do atendimento: gráfico diário (Chart.js 4.4.9, em static/admin).
   Os dados vêm no <script type="application/json"> da página (CSP script-src 'self').
   As cores saem dos tokens do CSS e são relidas quando o tema claro/escuro muda. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var tela = document.getElementById("grafico-dias");
    var fonte = document.getElementById("dados-grafico");
    if (!tela || !fonte || !window.Chart) return;  // sem gráfico: a tabela continua na página

    var dados = JSON.parse(fonte.textContent);
    var parado = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var grafico = new window.Chart(tela, {
      type: "bar",
      data: {
        labels: dados.rotulos,
        datasets: [
          { label: "Atendimentos", data: dados.atendimentos, borderRadius: 4 },
          { label: "Faltas", data: dados.faltas, borderRadius: 4 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: parado ? false : undefined,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { grid: { display: false }, ticks: {} },
          y: { beginAtZero: true, ticks: { precision: 0 }, grid: {} }
        },
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 12, boxHeight: 12 } }
        }
      }
    });

    function aplicarCores() {
      var css = getComputedStyle(document.documentElement);
      function cor(nome) { return css.getPropertyValue(nome).trim(); }
      var texto = cor("--texto-2");
      var o = grafico.options;
      grafico.data.datasets[0].backgroundColor = cor("--serie-atendidos");
      grafico.data.datasets[1].backgroundColor = cor("--serie-chegadas");
      o.scales.x.ticks.color = texto;
      o.scales.y.ticks.color = texto;
      o.scales.y.grid.color = cor("--borda");
      o.plugins.legend.labels.color = texto;
      grafico.update("none");
    }

    aplicarCores();
    var tema = window.matchMedia("(prefers-color-scheme: dark)");
    if (tema.addEventListener) tema.addEventListener("change", aplicarCores);
  });
})();
