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
});
