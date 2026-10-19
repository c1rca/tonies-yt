/** Read-only envelope renderer. Geometry is in CSS pixels, rasterized at the canvas DPR. */
function waveformBars(values, count) {
  const bars = [];
  for (let i = 0; i < count; i++) {
    const start = Math.floor(i * values.length / count);
    const end = Math.min(values.length, Math.max(start + 1, Math.ceil((i + 1) * values.length / count)));
    let amplitude = 0;
    for (let j = start; j < end; j++) {
      if (Number.isFinite(values[j])) amplitude = Math.max(amplitude, Math.min(1, values[j]));
    }
    // Fixed -54..-6 dB scale: quiet stays quiet after zoom; silence stays empty.
    bars.push(amplitude > 0 ? Math.max(0, Math.min(1, (20 * Math.log10(amplitude / 0.5) + 48) / 48)) : 0);
  }
  return bars;
}

function renderEditorBars(canvas, context, {levels, peaks, view, selection}, now, ratio, colors) {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  const count = Math.floor(width / 3);
  if (!count || !height) return;
  const values = Array.isArray(levels) && levels.length === peaks.length ? levels : peaks;
  const bars = waveformBars(values, count), baseline = Math.round(height * 0.7);
  for (let i = 0; i < bars.length; i++) {
    const time = view.start + ((i * 3 + 1) / width) * (view.end - view.start);
    context.fillStyle = time >= selection.start && time <= selection.end ? colors.accent : colors.muted;
    const h = Math.max(1, Math.round(bars[i] * (baseline - 8)));
    const x = i * 3 * ratio;
    context.globalAlpha = 1;
    context.fillRect(x, (baseline - h) * ratio, 2 * ratio, h * ratio);
    context.globalAlpha = 0.22;
    context.fillRect(x, (baseline + 2) * ratio, 2 * ratio, Math.max(1, Math.round(h * 0.32)) * ratio);
  }
  context.globalAlpha = 1;
  if (view.end > view.start && now >= view.start && now <= view.end) {
    context.fillStyle = colors.playhead;
    const x = (now - view.start) / (view.end - view.start) * width;
    context.fillRect(Math.min(width - 2, Math.max(0, x - 1)) * ratio, 0, 2 * ratio, height * ratio);
  }
}
