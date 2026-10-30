const state = { chart: null };

function pad(n) {
  return String(n).padStart(2, "0");
}

function defaultTimestamp() {
  const d = new Date();
  d.setMinutes(0, 0, 0);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:00`;
}

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta?.getAttribute("content") || "";
}

function csrfHeaders(extra) {
  return { "X-CSRFToken": csrfToken(), ...(extra || {}) };
}

async function readJson(res) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function setError(msg) {
  const el = document.getElementById("ingestError");
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = msg;
}

function renderKpis(summary, run) {
  if (!summary) return;
  document.getElementById("kpiMode").textContent = summary.active_mode || "—";
  document.getElementById("kpiModeMeta").textContent = summary.active_timestamp
    ? `Armed at ${summary.active_timestamp}`
    : "";
  document.getElementById("kpiDemand").textContent = summary.active_demand_kw ?? "—";
  document.getElementById("kpiPeak").textContent = summary.peak_demand_kw ?? "—";
  document.getElementById("kpiSoc").textContent = `${summary.battery_soc_pct ?? "—"}%`;
  document.getElementById("socFill").style.width = `${summary.battery_soc_pct ?? 0}%`;
  const hot = Boolean(summary.critical);
  document.getElementById("kpiOverload").textContent = hot ? "CRITICAL" : "Clear";
  document.getElementById("kpiOverloadMeta").textContent = hot
    ? `${summary.overload_hours} hour(s) at or above 200 kW`
    : "No hour at or above 200 kW";
  document.getElementById("overloadCard").classList.toggle("hot", hot);
  if (run?.model_source) {
    document.getElementById("modelChip").textContent = `model: ${run.model_source}`;
  }
}

function renderAlerts(alerts) {
  const dock = document.getElementById("alertDock");
  const critical = (alerts || []).filter((a) => a.level === "critical");
  if (!critical.length) {
    dock.hidden = true;
    dock.textContent = "";
    return;
  }
  dock.hidden = false;
  dock.textContent = critical[0].message;
}

function renderStrip(hours) {
  const root = document.getElementById("dispatchStrip");
  root.innerHTML = "";
  hours.forEach((h) => {
    const div = document.createElement("div");
    const cls = h.critical_overload ? "overload" : h.grid_mode.includes("Renewable") ? "storage" : "grid";
    div.className = `strip-hour ${cls}`;
    const hour = String(h.hour ?? String(h.timestamp).slice(11, 13)).padStart(2, "0");
    div.innerHTML = `<span>${hour}</span><strong>${Number(h.predicted_demand_kw).toFixed(0)}</strong>`;
    div.title = `${h.timestamp} · ${h.grid_mode} · ${h.reason}`;
    root.appendChild(div);
  });
}

function renderTable(hours) {
  const body = document.getElementById("logBody");
  if (!hours.length) {
    body.innerHTML = `<tr><td colspan="5" class="muted">No dispatch log yet.</td></tr>`;
    return;
  }
  body.innerHTML = hours
    .map((h) => {
      const modeClass = h.grid_mode.includes("Renewable") ? "storage" : "grid";
      const alert = h.critical_overload ? `<span class="badge alert">OVERLOAD</span>` : "—";
      return `<tr>
        <td>${h.timestamp}</td>
        <td>${Number(h.predicted_demand_kw).toFixed(2)}</td>
        <td><span class="badge ${modeClass}">${h.grid_mode}</span></td>
        <td>${h.battery_soc_pct}%</td>
        <td>${alert}</td>
      </tr>`;
    })
    .join("");
}

function renderChart(hours) {
  const ctx = document.getElementById("loadChart");
  const labels = hours.map((h) => String(h.timestamp).slice(11, 16));
  const demand = hours.map((h) => h.predicted_demand_kw);
  const soc = hours.map((h) => h.battery_soc_pct);
  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Demand (kW)",
          data: demand,
          borderColor: "#c9f24a",
          backgroundColor: "rgba(201, 242, 74, 0.12)",
          fill: true,
          tension: 0.35,
          yAxisID: "y",
        },
        {
          label: "Battery SOC (%)",
          data: soc,
          borderColor: "#7aa2ff",
          borderDash: [5, 4],
          tension: 0.3,
          yAxisID: "y1",
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: getComputedStyle(document.body).color } } },
      scales: {
        x: { ticks: { maxRotation: 0, color: "#8a9488" }, grid: { display: false } },
        y: { title: { display: true, text: "kW" }, grid: { color: "rgba(140,140,140,0.15)" } },
        y1: {
          position: "right",
          min: 0,
          max: 100,
          title: { display: true, text: "SOC %" },
          grid: { display: false },
        },
      },
    },
  });
}

function paint(payload) {
  const hours = payload.hours || [];
  renderKpis(payload.summary, payload.run || { model_source: payload.model_source });
  renderAlerts(payload.alerts || []);
  renderStrip(hours);
  renderTable(hours);
  if (hours.length) renderChart(hours);
  updateAssistantForecastStatus(hours.length > 0);
}

async function loadLatest() {
  const data = await readJson(await fetch("/api/forecast/latest"));
  paint(data);
}

function expandDay(startLocal, seed) {
  const start = new Date(startLocal);
  const rows = [];
  for (let i = 0; i < 24; i += 1) {
    const ts = new Date(start.getTime() + i * 3600 * 1000);
    const hour = ts.getHours();
    const solar = hour < 6 || hour > 19 ? 0 : Number(seed.solar_irradiance_wm2) * Math.max(0, Math.cos(((hour - 13) / 24) * Math.PI * 2));
    const tempDrift = Math.sin(((hour - 7) / 24) * Math.PI * 2) * 3.2;
    rows.push({
      timestamp: `${ts.getFullYear()}-${pad(ts.getMonth() + 1)}-${pad(ts.getDate())} ${pad(hour)}:00:00`,
      ambient_temp_c: Number((Number(seed.ambient_temp_c) + tempDrift).toFixed(2)),
      humidity_pct: Number(seed.humidity_pct),
      solar_irradiance_wm2: Number(Math.max(0, solar).toFixed(2)),
    });
  }
  return rows;
}

const assistantState = {
  hasForecast: false,
  initialized: false,
};

function appendChatMessage(role, text, source) {
  const container = document.getElementById("chatMessages");
  if (!container) return null;

  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${role}`;

  const roleTag = document.createElement("div");
  roleTag.className = "bubble-role";
  roleTag.textContent = role === "user" ? "You" : "Wattcast AI";
  bubble.appendChild(roleTag);

  const body = document.createElement("div");
  body.className = "bubble-text";
  body.textContent = text;
  bubble.appendChild(body);

  if (role !== "user" && source) {
    const meta = document.createElement("div");
    meta.className = "bubble-meta";
    meta.textContent = source === "llm" ? "Source: AI Model" : "Source: Rules Engine";
    bubble.appendChild(meta);
  }

  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
  return bubble;
}

function setAssistantSuggestions(suggestions) {
  const container = document.getElementById("chatSuggestions");
  if (!container) return;
  container.innerHTML = "";
  if (!suggestions || !suggestions.length) return;

  suggestions.forEach((text) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "suggestion-chip";
    chip.textContent = text;
    chip.addEventListener("click", () => {
      const input = document.getElementById("chatInput");
      if (input) {
        input.value = text;
        const form = document.getElementById("chatForm");
        if (form) form.dispatchEvent(new Event("submit"));
      }
    });
    container.appendChild(chip);
  });
}

function setChatLoading(loading) {
  const container = document.getElementById("chatMessages");
  const input = document.getElementById("chatInput");
  const btn = document.getElementById("chatSendBtn");
  const existing = document.getElementById("chatLoadingIndicator");

  if (loading) {
    if (input) input.disabled = true;
    if (btn) btn.disabled = true;
    if (!existing && container) {
      const bubble = document.createElement("div");
      bubble.className = "chat-bubble assistant loading";
      bubble.id = "chatLoadingIndicator";

      const roleTag = document.createElement("div");
      roleTag.className = "bubble-role";
      roleTag.textContent = "Wattcast AI";
      bubble.appendChild(roleTag);

      const body = document.createElement("div");
      body.className = "bubble-text";
      body.textContent = "Analyzing forecast data...";
      bubble.appendChild(body);

      container.appendChild(bubble);
      container.scrollTop = container.scrollHeight;
    }
  } else {
    if (existing) existing.remove();
    if (input) {
      input.disabled = false;
      input.focus();
    }
    if (btn) btn.disabled = false;
  }
}

function updateAssistantSourceChip(source) {
  const chip = document.getElementById("assistantSourceChip");
  if (!chip) return;
  chip.textContent = source === "llm" ? "AI Model (Active)" : "Rules Engine";
}

function updateAssistantForecastStatus(hasData) {
  assistantState.hasForecast = hasData;
  const notice = document.getElementById("assistantNoForecast");
  if (notice) {
    notice.hidden = Boolean(hasData);
  }
  if (!assistantState.initialized) {
    assistantState.initialized = true;
    if (hasData) {
      appendChatMessage(
        "assistant",
        "Hello! I am Wattcast AI, your smart energy dispatch assistant. 24-hour demand predictions and storage schedules are ready. Ask me about peak hours, overloads, or specific times like 'why at 6 pm'."
      );
      setAssistantSuggestions([
        "What is the peak hour?",
        "Why was storage used?",
        "Are there any overloads?",
        "Battery status",
      ]);
    } else {
      appendChatMessage(
        "assistant",
        "Hello! I am Wattcast AI. No forecast data is loaded yet. You can ask me about dispatch rules, or click 'Run sample day' in Overview to generate a live schedule."
      );
      setAssistantSuggestions([
        "What are the dispatch rules?",
        "How does battery storage work?",
        "What triggers an overload alert?",
      ]);
    }
  }
}

async function handleChatSubmit(ev) {
  ev.preventDefault();
  const input = document.getElementById("chatInput");
  const q = (input?.value || "").trim();
  if (!q) return;
  if (q.length > 500) {
    alert("Question must be 500 characters or fewer.");
    return;
  }

  input.value = "";
  appendChatMessage("user", q);
  setChatLoading(true);

  try {
    const res = await fetch("/api/assistant", {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ question: q }),
    });
    const data = await readJson(res);
    setChatLoading(false);

    appendChatMessage("assistant", data.answer || "No response received.", data.source);
    if (data.source) {
      updateAssistantSourceChip(data.source);
    }
    if (Array.isArray(data.suggestions) && data.suggestions.length) {
      setAssistantSuggestions(data.suggestions);
    }
  } catch (err) {
    setChatLoading(false);
    appendChatMessage(
      "assistant",
      `Error: ${err.message || "Could not reach Wattcast assistant. Please try again."}`
    );
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("formTs").value = defaultTimestamp();

  document.getElementById("csvForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    setError("");
    const file = document.getElementById("csvFile").files[0];
    if (!file) {
      setError("Choose a CSV file first.");
      return;
    }
    const body = new FormData();
    body.append("file", file);
    try {
      const data = await readJson(await fetch("/api/ingest/csv", { method: "POST", headers: csrfHeaders(), body }));
      paint(data);
    } catch (err) {
      setError(err.message);
    }
  });

  document.getElementById("sampleBtn").addEventListener("click", async () => {
    setError("");
    try {
      const data = await readJson(await fetch("/api/forecast/sample", { method: "POST", headers: csrfHeaders() }));
      paint(data);
    } catch (err) {
      setError(err.message);
    }
  });

  document.getElementById("liveForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    setError("");
    const form = ev.currentTarget;
    const seed = {
      timestamp: form.timestamp.value.replace("T", " ") + ":00",
      ambient_temp_c: form.ambient_temp_c.value,
      humidity_pct: form.humidity_pct.value,
      solar_irradiance_wm2: form.solar_irradiance_wm2.value,
    };
    const rows = document.getElementById("fillDay").checked
      ? expandDay(form.timestamp.value, seed)
      : [seed];
    try {
      const data = await readJson(
        await fetch("/api/ingest/form", {
          method: "POST",
          headers: csrfHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ rows }),
        })
      );
      paint(data);
    } catch (err) {
      setError(err.message);
    }
  });

  const chatForm = document.getElementById("chatForm");
  if (chatForm) {
    chatForm.addEventListener("submit", handleChatSubmit);
  }

  loadLatest().catch(() => {
    updateAssistantForecastStatus(false);
  });
});
