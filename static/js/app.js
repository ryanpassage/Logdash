/* LogDash — client-side behaviour */

window.logdash = (() => {
  const el = id => document.getElementById(id);

  /* ── Relative timestamps ─────────────────── */
  function formatRelative(isoStr) {
    if (!isoStr) return '—';
    const diff = Math.round((Date.now() - new Date(isoStr).getTime()) / 1000);
    if (diff < 5)  return 'just now';
    if (diff < 60) return `${diff}s ago`;
    const mins = Math.floor(diff / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24)  return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }

  function updateTimestamps() {
    document.querySelectorAll('.last-seen[data-ts]').forEach(el => {
      el.textContent = formatRelative(el.dataset.ts);
    });
  }

  /* ── Refresh indicator ───────────────────── */
  function onRefresh() {
    const dot = el('refresh-dot');
    const timeEl = el('refresh-time');
    if (timeEl) {
      timeEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
    if (dot) {
      dot.classList.add('active');
      setTimeout(() => dot.classList.remove('active'), 600);
    }
    updateTimestamps();
  }

  /* ── Init ────────────────────────────────── */
  function init() {
    updateTimestamps();
    // Tick relative timestamps every 15s
    setInterval(updateTimestamps, 15_000);
    // Show initial refresh time
    const timeEl = el('refresh-time');
    if (timeEl) {
      timeEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
  }

  document.addEventListener('DOMContentLoaded', init);
  // Also re-run after HTMX swaps content
  document.addEventListener('htmx:afterSwap', updateTimestamps);

  return { onRefresh };
})();

/* ── Chart utilities (server + pipeline detail pages) ─────────────── */
window.logdash = window.logdash || {};

(function () {
  const COLORS = {
    blue:   '#4f8ef7',
    green:  '#22d3a5',
    yellow: '#f0b429',
    red:    '#f4614a',
  };

  const BASE_SCALE = {
    grid: { color: 'rgba(148,163,192,0.07)' },
    ticks: { color: '#556078', font: { size: 10 }, maxTicksLimit: 8 },
  };

  const BASE_OPTS = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        labels: { color: '#94a3c0', font: { size: 11 }, boxWidth: 12, padding: 16 },
      },
      tooltip: {
        backgroundColor: '#111d30',
        borderColor: 'rgba(148,163,192,0.2)',
        borderWidth: 1,
        titleColor: '#e8f0fe',
        bodyColor: '#94a3c0',
        padding: 10,
      },
    },
    scales: {
      x: {
        type: 'time',
        time: {
          tooltipFormat: 'HH:mm:ss',
          displayFormats: { minute: 'HH:mm', hour: 'HH:mm' },
        },
        ...BASE_SCALE,
      },
      y: { beginAtZero: true, ...BASE_SCALE },
    },
  };

  function makeDataset(label, data, color, fill) {
    return {
      label,
      data,
      borderColor: color,
      backgroundColor: color + (fill ? '28' : '00'),
      borderWidth: 1.5,
      pointRadius: data.length > 120 ? 0 : 2,
      pointHoverRadius: 4,
      tension: 0.3,
      fill: fill || false,
    };
  }

  function renderChart(canvasId, datasets, yLabel) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    if (canvas._ldChart) canvas._ldChart.destroy();
    const opts = structuredClone(BASE_OPTS);
    if (yLabel) {
      opts.scales.y.title = { display: true, text: yLabel, color: '#556078', font: { size: 10 } };
    }
    canvas._ldChart = new Chart(canvas, { type: 'line', data: { datasets }, options: opts });
  }

  async function fetchSeries(url) {
    try {
      const res = await fetch(url);
      if (!res.ok) return [];
      return await res.json();
    } catch (_) {
      return [];
    }
  }

  window.logdash.loadServerCharts = async function (serverName, range) {
    const base = `/api/server/${encodeURIComponent(serverName)}/series`;
    const [evRows, jvmRows] = await Promise.all([
      fetchSeries(`${base}?metric=events&range=${range}`),
      fetchSeries(`${base}?metric=jvm&range=${range}`),
    ]);

    // Events chart
    renderChart('events-chart', [
      makeDataset('Events In/s',  evRows.map(r => ({ x: new Date(r.ts), y: r.events_in  ?? null })), COLORS.blue),
      makeDataset('Events Out/s', evRows.map(r => ({ x: new Date(r.ts), y: r.events_out ?? null })), COLORS.green),
    ], 'events / s');

    // JVM heap chart
    const heapRows = jvmRows.map(r => ({
      x: new Date(r.ts),
      y: r.heap_max_bytes > 0 ? Math.round((r.heap_used_bytes / r.heap_max_bytes) * 100) : null,
    }));
    renderChart('jvm-chart', [
      makeDataset('Heap %', heapRows, COLORS.yellow, true),
    ], 'heap %');
  };

  window.logdash.loadPipelineChart = async function (serverName, pipelineId, range) {
    const url = `/api/server/${encodeURIComponent(serverName)}/series`
      + `?metric=pipeline&pipeline=${encodeURIComponent(pipelineId)}&range=${range}`;
    const rows = await fetchSeries(url);
    renderChart('pipeline-chart', [
      makeDataset('Events In',  rows.map(r => ({ x: new Date(r.ts), y: r.events_in  ?? null })), COLORS.blue),
      makeDataset('Events Out', rows.map(r => ({ x: new Date(r.ts), y: r.events_out ?? null })), COLORS.green),
    ], 'events (cumulative)');
  };
}());
