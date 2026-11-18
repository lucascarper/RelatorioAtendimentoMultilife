/* Dashboards: gráficos diários (Chart.js 4.4.9, em static/admin).
   Cada <canvas data-grafico> tem ao lado um <script type="application/json" id="dados-…">
   com rótulos e séries (CSP script-src 'self'). Cada série diz o tipo (barra ou linha) e a
   cor como token do CSS; as cores são relidas quando o tema claro/escuro muda. */
(function () {
  "use strict";

  var parado = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var inteiro = new Intl.NumberFormat("pt-BR");
  var compacto = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
  var moeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

  function cor(nome) { return getComputedStyle(document.documentElement).getPropertyValue(nome).trim(); }

  function desenhar(tela) {
    var g = JSON.parse(document.getElementById("dados-" + tela.id).textContent);
    var dinheiro = g.formato === "moeda";
    var deitado = g.horizontal;  // barras deitadas: rótulos longos (grupos) no eixo y
    var rotulo = deitado ? "x" : "y";
    var escalaCategoria = { stacked: g.empilhado, grid: { display: false }, ticks: {} };
    var escalaValor = {
      stacked: g.empilhado,
      beginAtZero: true,
      grid: {},
      ticks: {
        precision: 0,
        callback: function (v) { return dinheiro ? "R$ " + compacto.format(v) : inteiro.format(v); }
      }
    };
    var grafico = new window.Chart(tela, {
      type: "bar",
      data: {
        labels: g.rotulos,
        datasets: g.series.map(function (s) {
          var linha = s.tipo === "line";
          return {
            type: linha ? "line" : "bar",
            label: s.rotulo,
            data: s.dados,
            backgroundColor: cor(s.cor),
            borderColor: cor(s.cor),
            order: linha ? 0 : 1,  // linha por cima das barras
            borderRadius: linha ? 0 : 4,
            borderWidth: linha ? 2 : 0,
            pointRadius: linha ? 2 : 0,
            tension: 0.25
          };
        })
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: parado ? false : undefined,
        indexAxis: deitado ? "y" : "x",
        interaction: { mode: "index", intersect: false, axis: deitado ? "y" : "x" },
        scales: deitado ? { x: escalaValor, y: escalaCategoria } : { x: escalaCategoria, y: escalaValor },
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 12, boxHeight: 12 } },
          tooltip: {
            callbacks: {
              label: function (c) {
                var v = c.parsed[rotulo];
                var texto = v === null ? "sem resumo" : dinheiro ? moeda.format(v) : inteiro.format(v);
                return c.dataset.label + ": " + texto;
              }
            }
          }
        }
      }
    });

    function aplicarCores() {
      var texto = cor("--texto-2");
      var o = grafico.options;
      g.series.forEach(function (s, i) {
        var c = cor(s.cor);
        grafico.data.datasets[i].backgroundColor = c;
        grafico.data.datasets[i].borderColor = c;
      });
      o.scales.x.ticks.color = texto;
      o.scales.y.ticks.color = texto;
      (deitado ? o.scales.x : o.scales.y).grid.color = cor("--borda");
      o.plugins.legend.labels.color = texto;
      grafico.update();
    }

    aplicarCores();
    return aplicarCores;
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!window.Chart) return;  // sem gráfico: as tabelas continuam na página
    var recolorir = Array.prototype.map.call(document.querySelectorAll("canvas[data-grafico]"), desenhar);
    var tema = window.matchMedia("(prefers-color-scheme: dark)");
    if (tema.addEventListener) {
      tema.addEventListener("change", function () { recolorir.forEach(function (f) { f(); }); });
    }
  });
})();
