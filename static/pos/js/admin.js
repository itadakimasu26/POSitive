document.addEventListener("DOMContentLoaded", () => {
  const search = document.getElementById("storeSearch");
  const status = document.getElementById("storeStatusFilter");
  const rows = Array.from(document.querySelectorAll(".store-row"));
  const empty = document.getElementById("filteredStoreEmpty");
  const count = document.getElementById("visibleStoreCount");

  if (search && status && rows.length) {
    const filterStores = () => {
      const query = search.value.trim().toLowerCase();
      const selectedStatus = status.value;
      let visible = 0;
      rows.forEach((row) => {
        const matchesText = !query || row.dataset.search.includes(query);
        const matchesStatus = selectedStatus === "all" || row.dataset.status === selectedStatus;
        row.hidden = !(matchesText && matchesStatus);
        if (!row.hidden) visible += 1;
      });
      empty.hidden = visible !== 0;
      count.textContent = `${visible} shown`;
    };
    search.addEventListener("input", filterStores);
    status.addEventListener("change", filterStores);
  }

  const plan = document.getElementById("id_active_plan");
  const existingAdministrator = document.getElementById("id_existing_administrator");
  if (plan && existingAdministrator) {
    const credentialFields = [
      "id_admin_username",
      "id_admin_email",
      "id_admin_password",
      "id_admin_password_confirm",
    ].map((id) => document.getElementById(id)).filter(Boolean);
    const updateAdministratorMode = () => {
      const isPro = plan.value === "Pro";
      existingAdministrator.disabled = !isPro;
      if (!isPro) existingAdministrator.value = "";
      const usesExisting = isPro && Boolean(existingAdministrator.value);
      credentialFields.forEach((field) => {
        field.disabled = usesExisting;
        field.closest(".form-row")?.classList.toggle("shared-admin-disabled", usesExisting);
      });
      existingAdministrator.closest(".form-row")?.classList.toggle("pro-option-disabled", !isPro);
    };
    plan.addEventListener("change", updateAdministratorMode);
    existingAdministrator.addEventListener("change", updateAdministratorMode);
    updateAdministratorMode();
  }

  const storeType = document.getElementById("id_store_type");
  const serviceCharge = document.getElementById("id_service_charge_rate");
  if (storeType && serviceCharge) {
    // Dining configuration belongs only to the Cafe operating workflow.
    const updateDiningSettings = () => {
      const supportsDining = storeType.value === "Cafe";
      const row = serviceCharge.closest(".form-row");
      serviceCharge.disabled = !supportsDining;
      if (!supportsDining) serviceCharge.value = "0.00";
      if (row) row.hidden = !supportsDining;
    };
    storeType.addEventListener("change", updateDiningSettings);
    updateDiningSettings();
  }
});
