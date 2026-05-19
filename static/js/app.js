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
