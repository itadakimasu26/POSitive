(function () {
  const shell = document.querySelector(".sell-layout");
  if (!shell) return;
  const taxRate = Number(shell.dataset.taxRate || 0);
  const barcodeScanning = shell.dataset.barcodeScanning === "true";
  const proEnabled = shell.dataset.pro === "true";
  const cart = new Map();
  const productButtons = Array.from(document.querySelectorAll(".product-card"));
  const grid = document.getElementById("productGrid");
  const cartLines = document.getElementById("cartLines");
  const cartJson = document.getElementById("cartJson");
  const paymentMethod = document.getElementById("paymentMethod");
  const checkoutButton = document.getElementById("checkoutButton");
  const checkoutForm = document.getElementById("checkoutForm");
  const productSearch = document.getElementById("productSearch");
  const customerSelect = document.getElementById("customerSelect");
  const discountRateInput = document.getElementById("discountRate");
  const loyaltyPointsInput = document.getElementById("loyaltyPoints");
  const money = new Intl.NumberFormat("en-PH", { style: "currency", currency: "PHP" });

  function readProduct(button) {
    return { id: Number(button.dataset.id), name: button.dataset.name, price: Number(button.dataset.price), stock: Number(button.dataset.stock), emoji: button.dataset.emoji, color: button.dataset.color };
  }

  function totals() {
    let subtotal = 0;
    cart.forEach(function (item) { subtotal += item.price * item.qty; });
    const discountRate = proEnabled ? Math.max(0, Math.min(50, Number(discountRateInput?.value || 0))) : 0;
    const discount = subtotal * discountRate / 100;
    const selectedCustomer = customerSelect?.selectedOptions[0];
    const availablePoints = Number(selectedCustomer?.dataset.points || 0);
    const requestedPoints = Math.max(0, Number(loyaltyPointsInput?.value || 0));
    const loyalty = Math.min(availablePoints, requestedPoints, Math.max(0, subtotal - discount));
    const taxable = Math.max(0, subtotal - discount - loyalty);
    const tax = taxable * taxRate / 100;
    return { subtotal, discount, loyalty, tax, total: taxable + tax };
  }

  function render() {
    const items = Array.from(cart.values());
    if (!items.length) {
      cartLines.innerHTML = '<div class="empty-state"><span>🛒</span><strong>Your cart is empty</strong><p>Tap a product to begin a sale.</p></div>';
    } else {
      cartLines.innerHTML = items.map(function (item) {
        return '<div class="cart-line"><span class="cart-art ' + item.color + '">' + item.emoji + '</span><div><strong>' + escapeHtml(item.name) + '</strong><small>' + money.format(item.price) + '</small><div class="stepper"><button type="button" data-qty="-1" data-id="' + item.id + '">−</button><span>' + item.qty + '</span><button type="button" data-qty="1" data-id="' + item.id + '">＋</button></div></div><b>' + money.format(item.price * item.qty) + '</b></div>';
      }).join("");
    }
    const value = totals();
    document.getElementById("subtotal").textContent = money.format(value.subtotal);
    if (proEnabled) {
      document.getElementById("discountValue").textContent = "−" + money.format(value.discount);
      document.getElementById("loyaltyValue").textContent = "−" + money.format(value.loyalty);
      document.getElementById("discountSummary").hidden = value.discount <= 0;
      document.getElementById("loyaltySummary").hidden = value.loyalty <= 0;
    }
    document.getElementById("tax").textContent = money.format(value.tax);
    document.getElementById("total").textContent = money.format(value.total);
    checkoutButton.querySelector("span").textContent = "Charge " + money.format(value.total);
    checkoutButton.disabled = !items.length;
    cartJson.value = JSON.stringify(items.map(function (item) { return { id: item.id, qty: item.qty }; }));
  }

  function escapeHtml(value) {
    const node = document.createElement("span");
    node.textContent = value;
    return node.innerHTML;
  }

  function addProduct(button) {
    const product = readProduct(button);
    const current = cart.get(product.id);
    if (current && current.qty >= product.stock) return;
    cart.set(product.id, current ? { ...current, qty: current.qty + 1 } : { ...product, qty: 1 });
    render();
  }

  productButtons.forEach(function (button) {
    button.addEventListener("click", function () { addProduct(button); });
  });

  cartLines.addEventListener("click", function (event) {
    const button = event.target.closest("button[data-qty]");
    if (!button) return;
    const id = Number(button.dataset.id);
    const item = cart.get(id);
    if (!item) return;
    item.qty = Math.min(item.stock, item.qty + Number(button.dataset.qty));
    if (item.qty <= 0) cart.delete(id); else cart.set(id, item);
    render();
  });

  document.getElementById("clearCart")?.addEventListener("click", function () { cart.clear(); render(); });
  discountRateInput?.addEventListener("input", render);
  loyaltyPointsInput?.addEventListener("input", render);
  customerSelect?.addEventListener("change", function () {
    const points = Number(customerSelect.selectedOptions[0]?.dataset.points || 0);
    loyaltyPointsInput.disabled = !customerSelect.value;
    loyaltyPointsInput.max = String(points);
    if (!customerSelect.value) loyaltyPointsInput.value = "0";
    render();
  });
  document.querySelectorAll("[data-payment]").forEach(function (button) {
    button.addEventListener("click", function () {
      document.querySelectorAll("[data-payment]").forEach(function (item) { item.classList.remove("active"); });
      button.classList.add("active");
      paymentMethod.value = button.dataset.payment;
    });
  });

  productSearch?.addEventListener("input", filterProducts);
  productSearch?.addEventListener("keydown", function (event) {
    if (!barcodeScanning || event.key !== "Enter") return;
    const query = productSearch.value.trim().toLowerCase();
    const exactMatch = productButtons.find(function (button) {
      return button.dataset.barcode.toLowerCase() === query && !button.disabled;
    });
    if (!exactMatch) return;
    event.preventDefault();
    addProduct(exactMatch);
    productSearch.value = "";
    filterProducts();
  });
  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      productSearch?.focus();
      productSearch?.select();
    }
  });
  document.querySelectorAll("[data-category]").forEach(function (button) {
    button.addEventListener("click", function () {
      document.querySelectorAll("[data-category]").forEach(function (item) { item.classList.remove("active"); });
      button.classList.add("active");
      filterProducts();
    });
  });

  function filterProducts() {
    const query = productSearch.value.trim().toLowerCase();
    const category = document.querySelector("[data-category].active")?.dataset.category || "All";
    productButtons.forEach(function (button) {
      const matchesQuery = button.dataset.name.toLowerCase().includes(query) || button.dataset.barcode.toLowerCase().includes(query);
      const matches = matchesQuery && (category === "All" || button.dataset.category === category);
      button.hidden = !matches;
    });
    grid.classList.toggle("filtered-empty", !productButtons.some(function (button) { return !button.hidden; }));
  }

  checkoutForm?.addEventListener("submit", function (event) {
    if (!cart.size) {
      event.preventDefault();
      return;
    }
    checkoutButton.disabled = true;
    checkoutButton.querySelector("span").textContent = "Processing…";
  });

  render();
})();
