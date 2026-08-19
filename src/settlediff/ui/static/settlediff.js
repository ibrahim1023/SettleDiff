document.addEventListener("click", (event) => {
  const source = event.target;
  if (!(source instanceof Element)) return;

  const copy = source.closest("[data-copy-artifact]");
  if (copy instanceof HTMLElement) {
    const target = document.querySelector(
      `[data-artifact-json="${copy.dataset.copyArtifact}"]`,
    );
    if (target && navigator.clipboard) {
      navigator.clipboard.writeText(target.textContent || "");
    }
  }

  const wrap = source.closest("[data-wrap-artifact]");
  if (wrap instanceof HTMLElement) {
    const target = document.querySelector(
      `[data-artifact-json="${wrap.dataset.wrapArtifact}"]`,
    );
    const enabled = target && target.classList.toggle("wrap-json");
    if (enabled !== null) {
      wrap.setAttribute("aria-pressed", String(Boolean(enabled)));
    }
  }
});
