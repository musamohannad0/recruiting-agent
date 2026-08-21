(function () {
  const toast = document.querySelector(".toast");
  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    window.setTimeout(() => toast.classList.remove("show"), 2600);
  }

  document.querySelectorAll("form[data-pending], form[method='post']").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!form.checkValidity()) return;
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "true";
      const button = event.submitter || form.querySelector("button[type='submit']");
      if (button) {
        button.dataset.originalLabel = button.innerHTML;
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.innerHTML = `<span class="spinner"></span>${button.dataset.pendingLabel || form.dataset.pendingLabel || "Working…"}`;
      }
      showToast(form.dataset.pendingMessage || "Received — the agent is processing it.");
    });
  });

  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const button = event.detail.elt;
    if (!(button instanceof HTMLButtonElement)) return;
    button.dataset.originalLabel = button.innerHTML;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.innerHTML = `<span class="spinner"></span>${button.dataset.pendingLabel || "Saving…"}`;
    showToast(button.dataset.pendingMessage || "Received — the agent is updating it.");
  });
  // An innerHTML swap destroys the button that was clicked, so focus falls to <body>.
  // Put it back on an equivalent control in the replacement markup.
  document.body.addEventListener("htmx:beforeSwap", (event) => {
    const button = event.detail.elt;
    if (button instanceof HTMLButtonElement) {
      button.dataset.refocusLabel = (button.textContent || "").trim();
    }
  });
  document.body.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail.target;
    const label = event.detail.requestConfig?.elt?.dataset?.refocusLabel;
    if (!(target instanceof HTMLElement)) return;
    const buttons = [...target.querySelectorAll("button")];
    const next = buttons.find((b) => b.textContent.trim() === label) || buttons[0];
    next?.focus();
  });

  document.body.addEventListener("htmx:responseError", (event) => {
    const button = event.detail.elt;
    if (!(button instanceof HTMLButtonElement)) return;
    button.disabled = false;
    button.removeAttribute("aria-busy");
    if (button.dataset.originalLabel) button.innerHTML = button.dataset.originalLabel;
    showToast("That did not save. Please try again.");
  });

  window.addEventListener("pageshow", () => {
    document.querySelectorAll("form[data-submitting='true']").forEach((form) => {
      form.dataset.submitting = "false";
      form.querySelectorAll("button[aria-busy='true']").forEach((button) => {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        if (button.dataset.originalLabel) button.innerHTML = button.dataset.originalLabel;
      });
    });
  });

  const upload = document.querySelector(".upload-zone input[type='file']");
  if (upload) {
    upload.addEventListener("change", () => {
      const label = document.querySelector("[data-upload-name]");
      if (label && upload.files.length) label.textContent = upload.files[0].name;
    });
  }

  const companyRoot = document.querySelector("[data-company-curation]");
  if (companyRoot) {
    const cards = [...companyRoot.querySelectorAll("[data-company-card]")];
    const count = companyRoot.querySelector("[data-selection-count]");
    const update = () => {
      let selected = 0;
      cards.forEach((card) => {
        const include = card.querySelector("input[name='included']");
        const priority = card.querySelector("input[name='priority']");
        card.classList.toggle("is-removed", !include.checked);
        priority.disabled = !include.checked;
        if (!include.checked) priority.checked = false;
        if (include.checked) selected += 1;
      });
      if (count) count.textContent = `${selected} selected`;
    };
    cards.forEach((card) => card.querySelector("input[name='included']").addEventListener("change", update));
    const filter = companyRoot.querySelector("[data-company-filter]");
    filter?.addEventListener("input", () => {
      const query = filter.value.trim().toLowerCase();
      cards.forEach((card) => { card.hidden = !card.dataset.search.includes(query); });
    });
    companyRoot.querySelectorAll("[data-bulk]").forEach((button) => {
      button.addEventListener("click", () => {
        const value = button.dataset.bulk === "all";
        cards.filter((card) => !card.hidden).forEach((card) => { card.querySelector("input[name='included']").checked = value; });
        update();
      });
    });
    update();
  }

  const onboardingPoll = document.querySelector("[data-onboarding-poll]");
  if (onboardingPoll) {
    const initialStage = onboardingPoll.dataset.stage;
    const poll = async () => {
      try {
        const response = await fetch("/onboarding/status", { headers: { "Accept": "application/json" } });
        const data = await response.json();
        if (data.stage !== initialStage || data.has_question || data.generation?.status === "failed") {
          window.location.reload();
          return;
        }
      } catch (_) {}
      window.setTimeout(poll, 1200);
    };
    window.setTimeout(poll, 700);
  }

  const bootPoll = document.querySelector("[data-boot-poll]");
  if (bootPoll) {
    let signature = bootPoll.dataset.signature || "";
    const poll = async () => {
      try {
        const response = await fetch("/getting-started/status", { headers: { "Accept": "application/json" } });
        const data = await response.json();
        const next = JSON.stringify(data);
        if (signature && next !== signature) window.location.reload();
        signature = next;
        if (data.cycle?.status === "success" || data.cycle?.status === "failed") return;
      } catch (_) {}
      window.setTimeout(poll, 1500);
    };
    window.setTimeout(poll, 900);
  }

  const activityRoot = document.querySelector("[data-activity-poll]");
  if (activityRoot) {
    let activeFilter = "all";

    // Delegated so the handlers survive an htmx swap of the whole body.
    const applyFilter = (filter) => {
      activeFilter = filter;
      activityRoot.querySelectorAll("[data-activity-filter]").forEach((button) => {
        const active = button.dataset.activityFilter === filter;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      let visible = 0;
      activityRoot.querySelectorAll("[data-activity-status]").forEach((row) => {
        row.hidden = filter !== "all" && row.dataset.activityStatus !== filter;
        if (!row.hidden) visible += 1;
      });
      const empty = activityRoot.querySelector("[data-filter-empty]");
      if (empty) empty.hidden = visible !== 0;
    };

    activityRoot.addEventListener("click", (event) => {
      const button = event.target.closest("[data-activity-filter]");
      if (button) applyFilter(button.dataset.activityFilter);
    });

    // A swap replaces every <details>, so remember which were open and restore them.
    const openDisclosures = new Set();
    const rememberDisclosures = () => {
      openDisclosures.clear();
      activityRoot
        .querySelectorAll("[data-disclosure-id][open]")
        .forEach((detail) => openDisclosures.add(detail.dataset.disclosureId));
    };
    const restoreDisclosures = () => {
      openDisclosures.forEach((id) => {
        activityRoot.querySelector(`[data-disclosure-id="${id}"]`)?.setAttribute("open", "");
      });
    };

    activityRoot.addEventListener("htmx:beforeSwap", rememberDisclosures);
    activityRoot.addEventListener("htmx:afterSwap", () => {
      restoreDisclosures();
      applyFilter(activeFilter);
      activityRoot.classList.remove("is-updating");
    });

    let activitySignature = activityRoot.dataset.signature;
    const pollActivity = async () => {
      // Back off while the tab is hidden; nobody is watching the numbers move.
      if (document.hidden) {
        window.setTimeout(pollActivity, 8000);
        return;
      }
      try {
        const response = await fetch("/activity/status", { headers: { Accept: "application/json" } });
        const data = await response.json();
        const livePhase = activityRoot.querySelector("[data-live-phase]");
        const liveStatus = activityRoot.querySelector("[data-live-status]");
        if (livePhase && livePhase.textContent !== data.phase_label) livePhase.textContent = data.phase_label;
        if (liveStatus && liveStatus.textContent !== data.status) {
          liveStatus.textContent = data.status;
          liveStatus.className = `live-status status-${data.status}`;
        }
        if (activitySignature && data.signature !== activitySignature) {
          activitySignature = data.signature;
          activityRoot.classList.add("is-updating");
          activityRoot.dispatchEvent(new CustomEvent("refresh-activity"));
        } else {
          activitySignature = data.signature;
        }
      } catch (_) {}
      window.setTimeout(pollActivity, 2500);
    };
    window.setTimeout(pollActivity, 1500);
    applyFilter("all");
  }
})();
