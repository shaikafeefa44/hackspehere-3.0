// Light / dark theme toggle (remembers the choice).
(function () {
  const root = document.documentElement;
  const btn = document.getElementById("themeToggle");
  function apply(t) {
    root.setAttribute("data-theme", t);
    if (btn) btn.textContent = t === "dark" ? "Light mode" : "Dark mode";
  }
  let saved = "dark";
  try { saved = localStorage.getItem("theme") || "dark"; } catch (e) {}
  apply(saved);
  if (btn) {
    btn.addEventListener("click", () => {
      const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      try { localStorage.setItem("theme", next); } catch (e) {}
      apply(next);
    });
  }
})();
