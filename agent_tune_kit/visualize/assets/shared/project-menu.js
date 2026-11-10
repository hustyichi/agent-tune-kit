(() => {
  "use strict";
  const choice = document.getElementById("project-choice");
  const summary = document.getElementById("project-summary");
  const position = () => {
    if (!choice.open) return;
    const menu = choice.querySelector(".project-menu");
    const rect = summary.getBoundingClientRect();
    const {clientWidth: width, clientHeight: height} = document.documentElement;
    if (!rect.height || rect.bottom <= 0 || rect.top >= height) { choice.open = false; return; }
    const below = Math.max(0, height - rect.bottom - 12), above = Math.max(0, rect.top - 12);
    const upwards = below < Math.min(320, menu.scrollHeight) && above > below;
    menu.style.maxHeight = `${Math.min(320, upwards ? above : below)}px`;
    menu.style.left = `${Math.max(8, Math.min(rect.left, width - menu.offsetWidth - 8))}px`;
    menu.style.top = `${upwards ? rect.top - menu.offsetHeight - 4 : rect.bottom + 4}px`;
  };
  choice.ontoggle = position;
  window.addEventListener("resize", position);
  document.addEventListener("scroll", event => {
    if (!event.target.closest?.(".project-menu")) position();
  }, true);
  // Capture dismissal before page controls can stop propagation or handle Escape themselves.
  document.addEventListener("click", event => {
    if (!event.target.closest("#project-choice")) choice.open = false;
  }, true);
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || !choice.open) return;
    event.preventDefault(); event.stopPropagation();
    choice.open = false;
    summary.focus();
  }, true);
})();
