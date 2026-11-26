(() => {
  const toggle = document.querySelector(".menu-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const open = document.body.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    });
    document.addEventListener("click", (event) => {
      if (document.body.classList.contains("nav-open")
        && !event.target.closest(".sidebar")
        && !event.target.closest(".menu-toggle")) {
        document.body.classList.remove("nav-open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  const filterByCategory = (category, activeItem) => {
    const rows = [...document.querySelectorAll(".api-row")];
    let visibleRows = 0;
    rows.forEach((row) => {
      row.hidden = row.dataset.apiCategory !== category;
      row.classList.remove("api-row-selected");
      const urlDetails = row.querySelector("[data-api-url-details]");
      if (urlDetails) urlDetails.hidden = true;
      if (!row.hidden) visibleRows += 1;
    });
    const emptyState = document.querySelector("[data-filter-empty]");
    if (emptyState) emptyState.hidden = visibleRows > 0;
    document.querySelectorAll(".sidebar .nav-item").forEach((item) => {
      item.classList.toggle("active", item === activeItem);
    });
  };

  document.querySelectorAll(".category-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const expand = button.getAttribute("aria-expanded") !== "true";
      document.querySelectorAll(".category-toggle").forEach((item) => {
        item.setAttribute("aria-expanded", String(item === button && expand));
        if (item !== button || !expand) {
          document.getElementById(item.getAttribute("aria-controls")).hidden = true;
        }
      });
      if (expand) {
        document.getElementById(button.getAttribute("aria-controls")).hidden = false;
      }
      filterByCategory(button.dataset.category, button);
    });
  });

  document.querySelectorAll(".category-link").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      filterByCategory(link.dataset.category, link);
      if (document.body.classList.contains("nav-open")) {
        document.body.classList.remove("nav-open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  });

  document.querySelectorAll(".category-endpoint-link").forEach((link) => {
    link.addEventListener("click", () => {
      if (document.body.classList.contains("nav-open")) {
        document.body.classList.remove("nav-open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  });

  document.querySelectorAll("[data-copy-url]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        if (!navigator.clipboard || !navigator.clipboard.writeText) {
          throw new Error("Clipboard access is unavailable in this browser context.");
        }
        await navigator.clipboard.writeText(button.dataset.copyUrl);
        button.textContent = "COPIED";
        button.title = "API URL copied to clipboard";
      } catch (error) {
        button.textContent = "COPY FAILED";
        button.title = error.message || "Unable to copy the API URL.";
      }
      window.setTimeout(() => {
        button.textContent = "COPY URL";
        button.title = "";
      }, 2000);
    });
  });

  const preview = document.querySelector("[data-api-preview]");
  const previewStatus = document.querySelector("[data-api-response-status]");
  const previewBody = document.querySelector("[data-api-response-body]");
  document.querySelectorAll(".api-preview-trigger").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!preview || !previewStatus || !previewBody) return;
      const method = button.dataset.apiMethod;
      const headers = { Accept: "application/json" };
      const options = { method, headers };
      if (method === "POST") {
        headers["Content-Type"] = "application/json";
        const csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (csrfInput) headers["X-CSRFToken"] = csrfInput.value;
        options.body = "{}";
      }

      document.querySelectorAll(".api-row").forEach((row) => {
        const selected = row.dataset.apiUrl === button.dataset.apiUrl;
        row.hidden = !selected;
        row.classList.toggle("api-row-selected", selected);
        const urlDetails = row.querySelector("[data-api-url-details]");
        if (urlDetails) urlDetails.hidden = !selected;
      });
      const emptyState = document.querySelector("[data-filter-empty]");
      if (emptyState) emptyState.hidden = true;
      button.disabled = true;
      preview.hidden = false;
      preview.scrollIntoView({ behavior: "smooth", block: "nearest" });
      previewStatus.textContent = `${method} ${button.dataset.apiName} · loading`;
      previewBody.textContent = "Loading…";
      try {
        const response = await fetch(button.dataset.apiUrl, options);
        const body = await response.text();
        let output = body || "(empty response)";
        try {
          output = JSON.stringify(JSON.parse(body), null, 2);
        } catch {
          // Keep non-JSON responses readable as plain text.
        }
        previewStatus.textContent = `HTTP ${response.status} · ${response.statusText}`;
        previewBody.textContent = output;
      } catch (error) {
        previewStatus.textContent = "REQUEST FAILED";
        previewBody.textContent = error.message || "Unable to reach this API.";
      } finally {
        button.disabled = false;
      }
    });
  });
  const closePreview = document.querySelector("[data-api-preview-close]");
  if (closePreview && preview) {
    closePreview.addEventListener("click", () => {
      preview.hidden = true;
    });
  }

  const stats = document.querySelectorAll("[data-stat]");
  if (stats.length) {
    fetch("/api/stats/", { headers: { Accept: "application/json" } })
      .then((response) => {
        if (!response.ok) throw new Error("Stats request failed");
        return response.json();
      })
      .then((data) => {
        stats.forEach((element) => {
          const value = data[element.dataset.stat];
          if (value !== undefined && value !== null) element.textContent = value;
        });
      })
      .catch(() => {
        stats.forEach((element) => {
          if (element.textContent === "—") element.textContent = "N/A";
        });
      });
  }
})();
