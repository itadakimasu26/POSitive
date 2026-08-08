document.addEventListener("DOMContentLoaded", () => {
  const storeType = document.getElementById("id_store_type");
  const serviceCharge = document.getElementById("id_service_charge_rate");
  const serviceChargeField = document.getElementById("trialServiceChargeField");
  if (!storeType || !serviceCharge || !serviceChargeField) return;

  // Keep the trial form aligned with the server rule: only Cafe stores have
  // dine-in/take-out checkout and therefore a configurable service charge.
  const updateDiningSettings = () => {
    const supportsDining = storeType.value === "Cafe";
    serviceCharge.disabled = !supportsDining;
    if (!supportsDining) serviceCharge.value = "0.00";
    serviceChargeField.hidden = !supportsDining;
  };

  storeType.addEventListener("change", updateDiningSettings);
  updateDiningSettings();
});
