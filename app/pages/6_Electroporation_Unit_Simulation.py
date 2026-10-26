import streamlit as st

from app.components.layout import init_page


PAGE_TITLE = "Step 6 - Electroporation Unit (Simulation)"

init_page(PAGE_TITLE)
st.title("Step 6 - Electroporation Unit (Simulation)")
st.caption("Interactive, educational simulator for electroporating Cas9 RNP into T-cells.")


HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Virtual CRISPR Lab: Electroporation Unit</title>
  <style>
    html, body { height: 100%; }
    body {
      font-family: 'Segoe UI', sans-serif;
      background-color: #f0f2f5;
      display: flex;
      height: 100%;
      margin: 0;
    }

    /* Layout */
    .sidebar {
      width: 300px;
      background: #2c3e50;
      color: white;
      padding: 20px;
      display: flex;
      flex-direction: column;
      box-sizing: border-box;
    }
    .main-stage {
      flex-grow: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      position: relative;
      padding: 24px;
      box-sizing: border-box;
    }

    /* UI Elements */
    h2 { border-bottom: 1px solid #7f8c8d; padding-bottom: 10px; margin: 0 0 10px 0; }
    label { display: block; margin-top: 15px; font-weight: bold; font-size: 0.9em; }
    input[type=range] { width: 100%; margin: 10px 0; }
    .value-display { float: right; color: #3498db; font-weight: 700; }
    .muted { color: rgba(255,255,255,0.75); font-size: 0.9em; line-height: 1.35; }

    .btn-run {
      background: #27ae60;
      color: white;
      border: none;
      padding: 15px;
      font-size: 1.1em;
      cursor: pointer;
      margin-top: auto;
      border-radius: 6px;
      transition: background 0.3s;
    }
    .btn-run:hover { background: #2ecc71; }

    /* The Cuvette (Visual Representation) */
    .cuvette-container {
      width: 220px;
      height: 320px;
      border: 4px solid #bdc3c7;
      border-radius: 0 0 10px 10px;
      position: relative;
      background: linear-gradient(#f6f7f9, #ecf0f1);
      overflow: hidden;
      box-shadow: 0 10px 20px rgba(0,0,0,0.1);
    }

    /* Local "cell texture" (no external URLs) */
    .cells {
      width: 100%;
      height: 100%;
      opacity: 0.55;
      transition: transform 0.5s, filter 0.5s, opacity 0.5s;
      background:
        radial-gradient(circle at 12% 18%, rgba(52,152,219,0.22) 0 4px, transparent 5px),
        radial-gradient(circle at 72% 26%, rgba(46,204,113,0.18) 0 3px, transparent 4px),
        radial-gradient(circle at 44% 62%, rgba(52,152,219,0.18) 0 3px, transparent 4px),
        radial-gradient(circle at 20% 78%, rgba(46,204,113,0.14) 0 2px, transparent 3px),
        radial-gradient(circle at 82% 74%, rgba(52,152,219,0.14) 0 2px, transparent 3px),
        repeating-radial-gradient(circle at 50% 50%, rgba(0,0,0,0.02) 0 2px, transparent 2px 8px);
      background-size: 120px 140px, 160px 160px, 140px 140px, 180px 180px, 200px 200px, 220px 220px;
      background-repeat: repeat;
    }

    .shock-overlay {
      position: absolute;
      top: 0; left: 0; right: 0; bottom: 0;
      background: #f1c40f;
      opacity: 0;
      pointer-events: none;
      mix-blend-mode: multiply;
    }

    /* Results Modal */
    .results {
      position: absolute;
      background: white;
      padding: 20px;
      border-radius: 10px;
      box-shadow: 0 20px 50px rgba(0,0,0,0.25);
      text-align: center;
      display: none;
      width: 320px;
      max-width: calc(100% - 30px);
    }
    .stat-box { display: flex; justify-content: space-between; margin: 10px 0; border-bottom: 1px solid #eee; padding-bottom: 6px; }
    .bar-container { height: 10px; background: #eee; width: 100%; margin-top: 5px; border-radius: 5px; overflow: hidden; }
    .bar-fill { height: 100%; transition: width 0.9s; }
    .btn-reset { margin-top: 15px; padding: 6px 14px; border-radius: 6px; border: 1px solid #d0d7de; background: #f6f8fa; cursor: pointer; }
    .btn-reset:hover { background: #eef2f6; }
  </style>
</head>
<body>
  <div class="sidebar">
    <h2>⚡ Electroporation</h2>
    <p class="muted">Adjust parameters to deliver Cas9 RNP into T-cells.</p>

    <label>Voltage (V) <span id="val-volt" class="value-display">0 V</span></label>
    <input type="range" id="voltage" min="0" max="1000" value="0" oninput="updateUI()">

    <label>Pulse Duration (ms) <span id="val-pulse" class="value-display">10 ms</span></label>
    <input type="range" id="pulse" min="1" max="50" value="10" oninput="updateUI()">

    <label>Cell Density (10⁶/ml) <span id="val-dense" class="value-display">5.0</span></label>
    <input type="range" id="density" min="1" max="10" step="0.5" value="5" oninput="updateUI()">

    <button class="btn-run" onclick="runExperiment()">PULSE CELLS</button>
  </div>

  <div class="main-stage">
    <div class="cuvette-container" aria-label="Electroporation cuvette">
      <div class="cells" id="cell-visual"></div>
      <div class="shock-overlay" id="shock-fx"></div>
    </div>

    <div class="results" id="result-panel" role="dialog" aria-modal="true">
      <h3 style="margin: 0 0 8px 0;">Experiment Results</h3>

      <div class="stat-box">
        <span>Transfection Efficiency:</span>
        <span id="res-eff">0%</span>
      </div>
      <div class="bar-container"><div id="bar-eff" class="bar-fill" style="background:#3498db; width:0%"></div></div>

      <div class="stat-box" style="margin-top: 14px;">
        <span>Cell Viability:</span>
        <span id="res-live">0%</span>
      </div>
      <div class="bar-container"><div id="bar-live" class="bar-fill" style="background:#e74c3c; width:0%"></div></div>

      <p id="res-comment" style="margin-top:20px; font-style:italic; font-size:0.92em;"></p>
      <button class="btn-reset" onclick="closeResults()">Reset</button>
    </div>
  </div>

  <script>
    function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }

    // Update labels as slider moves
    function updateUI() {
      const v = document.getElementById('voltage').value;
      const p = document.getElementById('pulse').value;
      const d = document.getElementById('density').value;
      document.getElementById('val-volt').innerText = v + " V";
      document.getElementById('val-pulse').innerText = p + " ms";
      document.getElementById('val-dense').innerText = parseFloat(d).toFixed(1);
    }

    function runExperiment() {
      // 1. Get Values
      const v = parseInt(document.getElementById('voltage').value, 10);
      const p = parseInt(document.getElementById('pulse').value, 10);
      const d = parseFloat(document.getElementById('density').value);

      // 2. Visual FX (The Shock)
      const shock = document.getElementById('shock-fx');
      shock.style.transition = `opacity ${Math.max(0.08, p/120)}s`;
      shock.style.opacity = '0.85';
      setTimeout(() => { shock.style.opacity = '0'; }, 120);

      const cells = document.getElementById('cell-visual');
      cells.style.transform = 'scale(1.02)';
      cells.style.filter = 'saturate(1.2) contrast(1.05)';
      setTimeout(() => {
        cells.style.transform = 'scale(1.0)';
        cells.style.filter = 'none';
      }, 220);

      // 3. THE SIMULATION LOGIC (Simplified)
      // Energy proxy: V * pulse
      const energy = v * p;

      // Density factor: best around ~5, penalize too low/high (simple Gaussian-ish curve)
      // Range: ~0.75 .. 1.0
      const densityPenalty = Math.exp(-Math.pow((d - 5.0) / 3.0, 2)); // 0..1
      const densityFactor = 0.75 + 0.25 * densityPenalty;

      let efficiency = 0;
      let viability = 100;

      // Efficiency: needs enough energy to open pores, then saturates
      if (energy < 1000) {
        efficiency = 0;
      } else {
        efficiency = Math.min(95, (energy - 1000) / 50);
      }
      efficiency = efficiency * densityFactor;

      // Viability: high energy damages fragile T-cells; density slightly worsens heat/field effects
      const damage = Math.max(0, (energy - 3000) / 40);
      const densityStress = (1.0 - densityFactor) * 18; // 0..~4.5
      viability = Math.max(0, 100 - damage - densityStress);

      // Interaction: if cells die, efficiency doesn't matter
      if (viability < 10) efficiency = 0;

      // 4. Show Results
      setTimeout(() => {
        showResults(Math.round(clamp(efficiency, 0, 100)), Math.round(clamp(viability, 0, 100)));
      }, 420);
    }

    function showResults(eff, live) {
      document.getElementById('result-panel').style.display = 'block';

      document.getElementById('res-eff').innerText = eff + "%";
      document.getElementById('bar-eff').style.width = eff + "%";

      document.getElementById('res-live').innerText = live + "%";
      document.getElementById('bar-live').style.width = live + "%";

      let comment = "";
      if (live < 20) comment = "⚠️ Severe cell death. Reduce voltage or pulse duration.";
      else if (eff < 10) comment = "⚠️ Low delivery. Increase voltage and/or pulse duration.";
      else if (eff > 70 && live > 70) comment = "✅ Strong balance: high delivery and high survival.";
      else comment = "ℹ️ Decent result—try nudging voltage/pulse to improve the trade-off.";

      document.getElementById('res-comment').innerText = comment;
    }

    function closeResults() {
      document.getElementById('result-panel').style.display = 'none';
    }

    // Initialize labels on load
    updateUI();
  </script>
</body>
</html>
"""

st.components.v1.html(HTML, height=760, scrolling=False)

