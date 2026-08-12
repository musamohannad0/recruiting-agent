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
    const filterButtons = [...activityRoot.querySelectorAll("[data-activity-filter]")];
    const actionRows = [...activityRoot.querySelectorAll("[data-activity-status]")];
    const filterEmpty = activityRoot.querySelector("[data-filter-empty]");
    const rememberDisclosures = () => {
      const open = [...activityRoot.querySelectorAll("[data-disclosure-id][open]")].map(
        (detail) => detail.dataset.disclosureId,
      );
      window.sessionStorage.setItem("activity-open", JSON.stringify(open));
    };
    try {
      const open = JSON.parse(window.sessionStorage.getItem("activity-open") || "[]");
      open.forEach((id) => activityRoot.querySelector(`[data-disclosure-id="${id}"]`)?.setAttribute("open", ""));
      window.sessionStorage.removeItem("activity-open");
    } catch (_) {}

    filterButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const filter = button.dataset.activityFilter;
        filterButtons.forEach((candidate) => {
          const active = candidate === button;
          candidate.classList.toggle("active", active);
          candidate.setAttribute("aria-pressed", String(active));
        });
        let visible = 0;
        actionRows.forEach((row) => {
          row.hidden = filter !== "all" && row.dataset.activityStatus !== filter;
          if (!row.hidden) visible += 1;
        });
        if (filterEmpty) filterEmpty.hidden = visible !== 0;
      });
    });

    activityRoot.querySelectorAll("[data-copy-payload]").forEach((button) => {
      button.addEventListener("click", async () => {
        const payload = button.closest(".payload-block")?.querySelector("pre")?.textContent || "";
        try {
          await navigator.clipboard.writeText(payload);
          button.textContent = "Copied";
          showToast("Recorded result copied.");
          window.setTimeout(() => { button.textContent = "Copy JSON"; }, 1400);
        } catch (_) {
          showToast("Could not copy this result.");
        }
      });
    });

    let activitySignature = activityRoot.dataset.signature;
    const pollActivity = async () => {
      if (document.hidden) {
        window.setTimeout(pollActivity, 2500);
        return;
      }
      try {
        const response = await fetch("/activity/status", { headers: { "Accept": "application/json" } });
        const data = await response.json();
        const livePhase = activityRoot.querySelector("[data-live-phase]");
        const liveStatus = activityRoot.querySelector("[data-live-status]");
        if (livePhase) livePhase.textContent = data.phase_label;
        if (liveStatus) {
          liveStatus.textContent = data.status;
          liveStatus.className = `live-status status-${data.status}`;
        }
        if (activitySignature && data.signature !== activitySignature) {
          rememberDisclosures();
          activityRoot.classList.add("is-updating");
          window.setTimeout(() => window.location.reload(), 180);
          return;
        }
        activitySignature = data.signature;
      } catch (_) {}
      window.setTimeout(pollActivity, 2500);
    };
    window.setTimeout(pollActivity, 1500);
  }
})();
