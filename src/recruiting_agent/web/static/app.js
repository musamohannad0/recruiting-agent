(function () {
  const toast = document.querySelector(".toast");
  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    window.setTimeout(() => toast.classList.remove("show"), 2600);
  }

  document.querySelectorAll("form[data-pending]").forEach((form) => {
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
      showToast(form.dataset.pendingMessage || "Received — Aster is working on it.");
    });
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
})();
