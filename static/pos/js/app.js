(function () {
  const sidebar = document.getElementById("sidebar");
  const menuButton = document.getElementById("menuButton");
  const menuScrim = document.getElementById("menuScrim");
  const themeToggle = document.getElementById("themeToggle");
  let modalTrigger = null;

  function updateThemeButton() {
    if (!themeToggle) return;
    const dark = document.documentElement.dataset.theme === "dark";
    themeToggle.querySelector("span").textContent = dark ? "☀" : "☾";
    themeToggle.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    themeToggle.setAttribute("aria-pressed", String(dark));
  }

  themeToggle?.addEventListener("click", function () {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    try { window.localStorage.setItem("positive-theme", nextTheme); } catch (error) { /* Storage may be unavailable. */ }
    updateThemeButton();
  });
  updateThemeButton();

  function closeMenu() {
    sidebar?.classList.remove("mobile-open");
    menuScrim?.classList.remove("open");
    document.body.classList.remove("menu-open");
    menuButton?.setAttribute("aria-expanded", "false");
  }

  menuButton?.addEventListener("click", function () {
    sidebar?.classList.add("mobile-open");
    menuScrim?.classList.add("open");
    document.body.classList.add("menu-open");
    menuButton.setAttribute("aria-expanded", "true");
  });
  menuScrim?.addEventListener("click", closeMenu);
  sidebar?.querySelectorAll("a").forEach(function (link) {
    link.addEventListener("click", closeMenu);
  });

  document.querySelectorAll(".message button").forEach(function (button) {
    button.addEventListener("click", function () { button.closest(".message")?.remove(); });
  });
  window.setTimeout(function () {
    document.querySelectorAll(".message").forEach(function (message) { message.remove(); });
  }, 4800);

  function focusableElements(modal) {
    return Array.from(modal.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'));
  }

  function openModal(id, trigger) {
    const modal = document.getElementById(id);
    if (!modal) return;
    modalTrigger = trigger || document.activeElement;
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    const dialog = modal.querySelector(".modal");
    dialog?.setAttribute("role", "dialog");
    dialog?.setAttribute("aria-modal", "true");
    dialog?.setAttribute("tabindex", "-1");
    window.requestAnimationFrame(function () {
      const preferredFocus = dialog?.querySelector(
        "input:not([type='hidden']):not([disabled]),select:not([disabled]),textarea:not([disabled])"
      );
      (preferredFocus || focusableElements(modal)[0] || dialog)?.focus({ preventScroll: true });
    });
  }

  function closeModal(modal) {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    if (!document.querySelector(".modal-backdrop.open")) document.body.classList.remove("modal-open");
    if (modalTrigger instanceof HTMLElement) modalTrigger.focus();
    modalTrigger = null;
  }

  document.querySelectorAll("[data-modal-open]").forEach(function (button) {
    button.addEventListener("click", function () { openModal(button.dataset.modalOpen, button); });
  });
  document.querySelectorAll("[data-modal-close]").forEach(function (button) {
    button.addEventListener("click", function () {
      const modal = button.closest(".modal-backdrop");
      if (modal) closeModal(modal);
    });
  });
  document.querySelectorAll(".modal-backdrop").forEach(function (modal) {
    modal.addEventListener("click", function (event) {
      if (event.target === modal) closeModal(modal);
    });
  });
  document.addEventListener("keydown", function (event) {
    const modal = document.querySelector(".modal-backdrop.open");
    if (event.key === "Escape") {
      if (modal) closeModal(modal);
      else if (sidebar?.classList.contains("mobile-open")) {
        closeMenu();
        menuButton?.focus();
      }
    }
    if (event.key === "Tab" && modal) {
      const focusable = focusableElements(modal);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  if (window.location.hash === "#new-product") openModal("productModal");

  const subscriptionForm = document.getElementById("subscriptionPaymentForm");
  if (subscriptionForm) {
    const planSelect = subscriptionForm.querySelector("[name='plan']");
    const methodSelect = subscriptionForm.querySelector("[name='payment_method']");
    const amount = document.getElementById("subscriptionAmount");
    const prices = { Starter: 399, Pro: 799 };
    const updateSubscriptionPayment = function () {
      const selectedMethod = methodSelect?.value;
      document.querySelectorAll("[data-payment-panel]").forEach(function (panel) {
        panel.hidden = panel.dataset.paymentPanel !== selectedMethod;
      });
      if (amount && planSelect) {
        amount.textContent = new Intl.NumberFormat("en-PH", {
          style: "currency",
          currency: "PHP",
        }).format(prices[planSelect.value] || 0);
      }
    };
    planSelect?.addEventListener("change", updateSubscriptionPayment);
    methodSelect?.addEventListener("change", updateSubscriptionPayment);
    updateSubscriptionPayment();
  }

  window.POSitive = { openModal: openModal };
})();
