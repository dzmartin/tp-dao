const state = { data: null, currentView: "inicio" };
const currency = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS", maximumFractionDigits: 2 });
const dateTime = new Intl.DateTimeFormat("es-AR", { dateStyle: "short", timeStyle: "short" });
const shortDate = new Intl.DateTimeFormat("es-AR", { dateStyle: "short" });

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

function money(cents) {
  return currency.format((Number(cents) || 0) / 100);
}

function emptyRow(columns, message = "Todavía no hay registros.") {
  return `<tr><td class="empty-cell" colspan="${columns}">${escapeHtml(message)}</td></tr>`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "No se pudo completar la operación.");
  return payload;
}

function toast(message, isError = false) {
  const notice = document.createElement("div");
  notice.className = `toast${isError ? " error" : ""}`;
  notice.textContent = message;
  document.querySelector("#toast-region").append(notice);
  window.setTimeout(() => notice.remove(), 4300);
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

async function submitForm(form, path, transform = (value) => value, success = "Operación realizada.") {
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const body = transform(formObject(form));
    const result = await api(path, { method: "POST", body: JSON.stringify(body) });
    form.reset();
    await refreshData();
    toast(typeof success === "function" ? success(result.result) : success);
    return result;
  } catch (error) {
    toast(error.message, true);
    return null;
  } finally {
    button.disabled = false;
  }
}

function setOptions(select, options, firstLabel, selectedValue = "") {
  if (!select) return;
  const previous = selectedValue || select.value;
  select.replaceChildren();
  if (firstLabel !== null) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = firstLabel;
    select.append(placeholder);
  }
  for (const item of options) {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    option.disabled = Boolean(item.disabled);
    select.append(option);
  }
  if (options.some((option) => String(option.value) === String(previous))) select.value = previous;
}

function productOptions(locationCode = "", sellableOnly = false) {
  return (state.data?.products || []).map((product) => {
    const stock = state.data.stock.find((item) => item.location_code === locationCode && item.sku === product.sku);
    const availability = locationCode ? ` · ${stock?.quantity ?? 0} disp.` : "";
    return { value: product.sku, label: `${product.sku} · ${product.name}${availability}`, disabled: sellableOnly && (!stock || stock.quantity < 1) };
  });
}

function addItemRow(container, selectedSku = "") {
  const row = document.createElement("div");
  row.className = "entry-row";
  const product = document.createElement("select");
  product.name = "sku";
  product.required = true;
  for (const item of productOptions()) {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    product.append(option);
  }
  if (selectedSku) product.value = selectedSku;
  const quantity = document.createElement("input");
  quantity.name = "quantity";
  quantity.type = "number";
  quantity.min = "1";
  quantity.step = "1";
  quantity.value = "1";
  quantity.required = true;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-row";
  remove.textContent = "Quitar";
  remove.addEventListener("click", () => {
    if (container.children.length > 1) row.remove();
    updateSaleTotal();
  });
  row.append(product, quantity, remove);
  container.append(row);
  row.addEventListener("input", updateSaleTotal);
  row.addEventListener("change", updateSaleTotal);
  return row;
}

function addPaymentRow() {
  const container = document.querySelector("#payment-rows");
  const row = document.createElement("div");
  row.className = "payment-row";
  const method = document.createElement("select");
  method.name = "method";
  method.required = true;
  for (const [value, label] of [["efectivo", "Efectivo"], ["tarjeta", "Tarjeta"], ["transferencia", "Transferencia"]]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    method.append(option);
  }
  const amount = document.createElement("input");
  amount.name = "amount";
  amount.type = "number";
  amount.min = "0.01";
  amount.step = "0.01";
  amount.required = true;
  amount.placeholder = "0,00";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-row";
  remove.textContent = "Quitar";
  remove.addEventListener("click", () => {
    if (container.children.length > 1) row.remove();
    syncPaymentOptions();
  });
  method.addEventListener("change", syncPaymentOptions);
  row.append(method, amount, remove);
  container.append(row);
  syncPaymentOptions();
  return row;
}

function syncPaymentOptions() {
  const rows = [...document.querySelectorAll(".payment-row")];
  const selected = rows.map((row) => row.querySelector('[name="method"]').value);
  rows.forEach((row, index) => {
    [...row.querySelector("select").options].forEach((option) => {
      option.disabled = selected.includes(option.value) && option.value !== selected[index];
    });
  });
}

function readItemRows(containerId) {
  const rows = [...document.querySelectorAll(`#${containerId} .entry-row`)];
  const items = rows.map((row) => ({
    sku: row.querySelector('[name="sku"]').value,
    quantity: Number(row.querySelector('[name="quantity"]').value),
  }));
  if (items.some((item) => !item.sku || !Number.isInteger(item.quantity) || item.quantity < 1)) {
    throw new Error("Completá cada producto y una cantidad entera mayor a cero.");
  }
  if (new Set(items.map((item) => item.sku)).size !== items.length) {
    throw new Error("Cada producto debe aparecer una sola vez; modifica su cantidad en la misma línea.");
  }
  return items;
}

function updateSaleTotal() {
  const location = document.querySelector('#sale-form [name="location_code"]')?.value || "";
  const rows = [...document.querySelectorAll("#sale-items .entry-row")];
  const total = rows.reduce((sum, row) => {
    const sku = row.querySelector('[name="sku"]').value;
    const quantity = Number(row.querySelector('[name="quantity"]').value) || 0;
    const product = state.data?.products.find((item) => item.sku === sku);
    return sum + (product?.price_cents || 0) * quantity;
  }, 0);
  document.querySelector("#sale-total").textContent = money(total);
  if (location) {
    rows.forEach((row) => {
      const select = row.querySelector('[name="sku"]');
      const selected = select.value;
      setOptions(select, productOptions(location), null, selected);
    });
  }
}

function populateSelects() {
  const locations = state.data.locations;
  const locals = locations.filter((item) => item.type === "LOCAL");
  const suppliers = state.data.suppliers;
  const supplierSelect = document.querySelector("#product-supplier");
  const supplierValue = supplierSelect.value;
  setOptions(supplierSelect, suppliers.map((item) => ({ value: item.id, label: `${item.name} · ${item.tax_id}` })), "Sin proveedor", supplierValue);
  document.querySelectorAll(".location-select").forEach((select) => {
    const current = select.value;
    setOptions(select, locations.map((item) => ({ value: item.code, label: `${item.code} · ${item.name}` })), "Elegir ubicación", current);
  });
  document.querySelectorAll(".local-select").forEach((select) => {
    const current = select.value;
    setOptions(select, locals.map((item) => ({ value: item.code, label: `${item.code} · ${item.name}` })), "Elegir local", current);
  });
  const stockFilter = document.querySelector("#stock-filter");
  const currentFilter = stockFilter.value;
  setOptions(stockFilter, locations.map((item) => ({ value: item.code, label: `${item.code} · ${item.name}` })), "Todas las ubicaciones", currentFilter);
  const orderSelect = document.querySelector("#package-order");
  const orderValue = orderSelect.value;
  const openOrders = state.data.orders.filter((order) => ["Abierta", "Preparando", "Con rechazo"].includes(order.status));
  setOptions(orderSelect, openOrders.map((item) => ({ value: item.id, label: `${item.code} · ${item.destination_code} · ${item.status}` })), "Elegir orden", orderValue);
  document.querySelectorAll("#order-items .entry-row select, #package-items .entry-row select").forEach((select) => {
    const current = select.value;
    setOptions(select, productOptions(), null, current);
  });
  document.querySelectorAll("#sale-items .entry-row select").forEach((select) => {
    const current = select.value;
    setOptions(select, productOptions(document.querySelector('#sale-form [name="location_code"]').value, true), null, current);
  });
  updateSaleTotal();
}

function renderDashboard() {
  const { summary } = state.data;
  document.querySelector("#metric-sales").textContent = money(summary.today_net_cents);
  document.querySelector("#metric-products").textContent = summary.product_count;
  document.querySelector("#metric-low-stock").textContent = summary.low_stock_count;
  document.querySelector("#metric-transit").textContent = summary.in_transit_count;
  const packageRows = state.data.packages.slice(0, 6).map((item) => {
    const event = item.events.at(-1);
    return `<tr><td>${escapeHtml(item.code)}<br><small>${escapeHtml(item.dispatch_note)}</small></td><td>${escapeHtml(item.destination_code)}</td><td><span class="status-pill" data-status="${escapeHtml(item.status)}">${escapeHtml(item.status)}</span></td><td>${escapeHtml(event?.detail || "Sin eventos")}</td></tr>`;
  });
  document.querySelector("#dashboard-packages").innerHTML = packageRows.join("") || emptyRow(4, "Todavía no hay bultos en seguimiento.");
  const sales = state.data.sales.slice(0, 5).map((sale) => `<div class="recent-sale"><div><strong>${escapeHtml(sale.receipt)}</strong><small>${escapeHtml(sale.location_code)} · ${dateTime.format(new Date(sale.sold_at))}</small></div><span class="recent-amount">${money(sale.total_cents)}</span></div>`);
  document.querySelector("#dashboard-sales").innerHTML = sales.join("") || '<p class="empty-cell">Todavía no hay ventas.</p>';
}

function renderCatalog() {
  const search = document.querySelector("#product-search").value.trim().toLowerCase();
  const products = state.data.products.filter((item) => `${item.sku} ${item.name} ${item.category}`.toLowerCase().includes(search));
  document.querySelector("#products-table").innerHTML = products.map((item) => `<tr><td>${escapeHtml(item.sku)}</td><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.category)}</td><td>${escapeHtml(item.supplier_name || "—")}</td><td>${money(item.cost_cents)}</td><td>${money(item.price_cents)}</td><td>${item.minimum_stock}</td></tr>`).join("") || emptyRow(7, "No hay productos que coincidan.");
  document.querySelector("#locations-list").innerHTML = state.data.locations.map((item) => `<div class="location-chip"><b>${item.type === "CD" ? "CD" : escapeHtml(item.code.slice(0, 3))}</b><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.code)} · ${item.type === "CD" ? "Centro de Distribución" : "Local"}</small></span></div>`).join("") || '<p class="empty-cell">Agrega el primer CD o local.</p>';
  document.querySelector("#suppliers-table").innerHTML = state.data.suppliers.map((item) => `<tr><td>${escapeHtml(item.tax_id)}</td><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.phone || "—")}</td><td>${escapeHtml(item.email || "—")}</td></tr>`).join("") || emptyRow(4, "Todavía no hay proveedores.");
}

function renderInventory() {
  const filter = document.querySelector("#stock-filter").value;
  const rows = state.data.stock.filter((item) => !filter || item.location_code === filter);
  document.querySelector("#stock-table").innerHTML = rows.map((item) => `<tr><td>${escapeHtml(item.location_code)}</td><td>${escapeHtml(item.sku)}</td><td>${escapeHtml(item.name)}</td><td class="${item.quantity <= item.minimum_stock ? "stock-low" : ""}">${item.quantity}</td><td>${item.minimum_stock}</td><td>${money(item.quantity * item.cost_cents)}</td></tr>`).join("") || emptyRow(6, "No hay existencias para mostrar.");
  document.querySelector("#orders-table").innerHTML = state.data.orders.map((order) => `<tr><td>${escapeHtml(order.code)}</td><td>${escapeHtml(order.origin_code)}</td><td>${escapeHtml(order.destination_code)}</td><td>${order.lines.map((line) => `${escapeHtml(line.sku)} × ${line.quantity}`).join(", ")}</td><td><span class="status-pill" data-status="${escapeHtml(order.status)}">${escapeHtml(order.status)}</span></td><td>${shortDate.format(new Date(order.created_at))}</td></tr>`).join("") || emptyRow(6, "Todavía no hay órdenes de reposición.");
}

function packageActionButtons(item) {
  if (item.status === "Preparado") return `<button class="small-action" data-package-action="seal" data-code="${escapeHtml(item.code)}">Precintar</button>`;
  if (item.status === "Precintado") return `<button class="small-action" data-package-action="dispatch" data-code="${escapeHtml(item.code)}">Despachar</button>`;
  if (item.status === "En tránsito") return `<button class="small-action" data-package-action="receive" data-code="${escapeHtml(item.code)}">Registrar recepción</button>`;
  if (item.status === "Recibido") return `<button class="small-action" data-package-action="verify" data-code="${escapeHtml(item.code)}">Verificar y liberar</button>`;
  return "";
}

function renderPackages() {
  const filter = document.querySelector("#package-filter").value;
  const packages = state.data.packages.filter((item) => !filter || item.status === filter);
  document.querySelector("#packages-list").innerHTML = packages.map((item) => `<article class="package-card">
    <div class="package-identity"><strong>${escapeHtml(item.code)}</strong><small>Remito ${escapeHtml(item.dispatch_note)} · Orden ${escapeHtml(item.order_code)}</small></div>
    <div class="package-destination"><strong>${escapeHtml(item.destination_code)}</strong><small>Precinto ${escapeHtml(item.seal || "Pendiente")}</small></div>
    <div><span class="status-pill" data-status="${escapeHtml(item.status)}">${escapeHtml(item.status)}</span><div class="package-lines">${item.lines.map((line) => `${escapeHtml(line.sku)} × ${line.quantity}`).join(" · ")}</div></div>
    <div class="package-actions">${packageActionButtons(item)}</div>
    <details class="events-detail"><summary>Historial inmutable · ${item.events.length} eventos</summary><ol>${item.events.map((event) => `<li><strong>${escapeHtml(event.state)}</strong> · ${escapeHtml(event.detail)} · ${dateTime.format(new Date(event.occurred_at))}</li>`).join("")}</ol></details>
  </article>`).join("") || '<p class="empty-cell">No hay bultos para este filtro.</p>';
}

function renderSales() {
  document.querySelector("#sales-table").innerHTML = state.data.sales.map((sale) => `<tr><td><span class="receipt-code">${escapeHtml(sale.receipt)}</span><br><small>${escapeHtml(sale.payment_methods || "")}</small></td><td>${escapeHtml(sale.location_code)}<br><small>${dateTime.format(new Date(sale.sold_at))}</small></td><td class="sale-items-summary">${sale.lines.map((line) => `${escapeHtml(line.name)} × ${line.quantity}`).join(", ")}</td><td><strong>${money(sale.total_cents)}</strong></td><td><button class="return-button" data-return="${escapeHtml(sale.receipt)}">Devolver</button></td></tr>`).join("") || emptyRow(5, "Todavía no hay comprobantes.");
}

function renderAll() {
  populateSelects();
  renderDashboard();
  renderCatalog();
  renderInventory();
  renderPackages();
  renderSales();
}

async function refreshData() {
  state.data = await api("/api/bootstrap");
  renderAll();
}

function navigate(view) {
  state.currentView = view;
  document.querySelectorAll(".page-view").forEach((element) => element.classList.toggle("active", element.id === `view-${view}`));
  document.querySelectorAll(".nav-item").forEach((element) => element.classList.toggle("active", element.dataset.view === view));
  const active = document.querySelector(`.nav-item[data-view="${view}"]`);
  document.querySelector("#breadcrumb-current").textContent = active?.textContent.replace(/^\d+/, "").trim().toUpperCase() || view.toUpperCase();
  if (view === "reportes") loadReports();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadReports(start, end) {
  const form = document.querySelector("#report-form");
  const values = start && end ? { start, end } : formObject(form);
  if (!values.start || !values.end) return;
  try {
    const report = await api(`/api/reports?start=${encodeURIComponent(values.start)}&end=${encodeURIComponent(values.end)}`);
    document.querySelector("#report-sales").innerHTML = report.sales.map((row) => `<tr><td>${escapeHtml(row.activity_date)}</td><td>${escapeHtml(row.location)}</td><td>${escapeHtml(row.payment_method)}</td><td>${money(row.net_cents)}</td></tr>`).join("") || emptyRow(4, "Sin ventas en este período.");
    document.querySelector("#report-products").innerHTML = report.best_sellers.map((row) => `<tr><td>${escapeHtml(row.sku)}<br><small>${escapeHtml(row.name)}</small></td><td>${row.net_units}</td><td>${money(row.net_revenue_cents)}</td><td>${money(row.margin_cents)}</td></tr>`).join("") || emptyRow(4, "Sin ventas en este período.");
    document.querySelector("#report-inventory").innerHTML = report.inventory.map((row) => `<tr><td>${escapeHtml(row.location)}</td><td>${escapeHtml(row.sku)}</td><td>${escapeHtml(row.name)}</td><td>${row.quantity}</td><td>${row.minimum_stock}</td><td>${money(row.valuation_cents)}</td><td>${row.at_minimum ? '<span class="status-pill" data-status="Rechazado">Reponer</span>' : '<span class="status-pill" data-status="Verificado">Normal</span>'}</td></tr>`).join("") || emptyRow(7, "No hay locales o productos.");
    const logistics = report.logistics;
    const average = logistics.average_transit_hours == null ? "—" : `${Number(logistics.average_transit_hours).toFixed(1)} h`;
    document.querySelector("#report-logistics").innerHTML = `<div class="logistic-stat"><strong>${logistics.rejected_count}</strong><small>Bultos rechazados</small></div><div class="logistic-stat"><strong>${logistics.verified_packages}</strong><small>Bultos verificados</small></div><div class="logistic-stat"><strong>${average}</strong><small>Tránsito promedio</small></div><div class="rejection-list">${logistics.rejected_packages.map((item) => `<p><b>${escapeHtml(item.code)}</b> · ${escapeHtml(item.destination)} · ${escapeHtml(item.reason || "Rechazado")}</p>`).join("") || "<small>Sin rechazos registrados.</small>"}</div>`;
    document.querySelector("#report-movements").innerHTML = report.movements.map((row) => `<tr><td>${escapeHtml(row.movement_date)}</td><td>${escapeHtml(row.location)}</td><td>${escapeHtml(row.sku)}</td><td>${escapeHtml(row.movement_type)}</td><td>${row.quantity_delta > 0 ? "+" : ""}${row.quantity_delta}</td><td>${money(row.valuation_delta_cents)}</td></tr>`).join("") || emptyRow(6, "Sin movimientos en este período.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function handlePackageAction(button) {
  const { packageAction: action, code } = button.dataset;
  try {
    let result;
    if (action === "seal") {
      const seal = window.prompt(`Ingresá el precinto para ${code}:`);
      if (seal === null) return;
      result = await api(`/api/packages/${encodeURIComponent(code)}/seal`, { method: "POST", body: JSON.stringify({ seal }) });
    } else if (action === "dispatch") {
      if (!window.confirm(`¿Confirmás el despacho del bulto ${code} desde el CD?`)) return;
      result = await api(`/api/packages/${encodeURIComponent(code)}/dispatch`, { method: "POST", body: "{}" });
    } else if (action === "receive") {
      const sealIntact = window.confirm(`¿El precinto de ${code} llegó intacto?\nAceptar: intacto · Cancelar: roto`);
      const dispatchNoteOk = window.confirm(`¿El remito del bulto ${code} es conforme?\nAceptar: sí · Cancelar: no`);
      result = await api(`/api/packages/${encodeURIComponent(code)}/receive`, { method: "POST", body: JSON.stringify({ seal_intact: sealIntact, dispatch_note_ok: dispatchNoteOk }) });
    } else {
      if (!window.confirm(`Verificar contenido y liberar stock del bulto ${code}?`)) return;
      result = await api(`/api/packages/${encodeURIComponent(code)}/verify`, { method: "POST", body: "{}" });
    }
    await refreshData();
    toast(typeof result.result === "string" ? result.result : `Bulto ${code}: ${result.result}`);
  } catch (error) {
    toast(error.message, true);
  }
}

function openReturnDialog(receipt) {
  const sale = state.data.sales.find((item) => item.receipt === receipt);
  if (!sale) return;
  const select = document.querySelector("#return-sku");
  select.replaceChildren();
  for (const line of sale.lines.filter((item) => item.quantity > item.returned_quantity)) {
    const option = document.createElement("option");
    option.value = line.sku;
    option.textContent = `${line.sku} · ${line.name} · quedan ${line.quantity - line.returned_quantity}`;
    option.dataset.remaining = String(line.quantity - line.returned_quantity);
    select.append(option);
  }
  if (!select.options.length) {
    toast("Este comprobante ya no tiene unidades disponibles para devolver.", true);
    return;
  }
  document.querySelector('#return-form [name="receipt"]').value = receipt;
  document.querySelector("#return-receipt").textContent = receipt;
  const quantity = document.querySelector('#return-form [name="quantity"]');
  quantity.value = "1";
  quantity.max = select.selectedOptions[0].dataset.remaining;
  select.onchange = () => { quantity.max = select.selectedOptions[0].dataset.remaining; };
  document.querySelector("#return-dialog").showModal();
}

function bindForms() {
  document.querySelector("#location-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/api/locations", undefined, "Ubicación creada.");
  });
  document.querySelector("#supplier-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/api/suppliers", undefined, "Proveedor creado.");
  });
  document.querySelector("#product-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/api/products", undefined, "Producto creado.");
  });
  document.querySelector("#stock-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/api/stock", (body) => ({ ...body, delta: Number(body.delta) }), "Movimiento de stock aplicado.");
  });
  document.querySelector("#order-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const body = formObject(event.currentTarget);
      body.items = readItemRows("order-items").reduce((result, item) => ({ ...result, [item.sku]: item.quantity }), {});
      const response = await submitForm(event.currentTarget, "/api/orders", () => body, (id) => `Orden creada. Número interno: ${id}.`);
      if (response) { document.querySelector("#order-items").replaceChildren(); addItemRow(document.querySelector("#order-items")); }
    } catch (error) { toast(error.message, true); }
  });
  document.querySelector("#package-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const body = formObject(event.currentTarget);
      body.items = readItemRows("package-items").reduce((result, item) => ({ ...result, [item.sku]: item.quantity }), {});
      body.order_id = Number(body.order_id);
      const response = await submitForm(event.currentTarget, "/api/packages", () => body, (id) => `Bulto preparado. Identificador: ${id}.`);
      if (response) { document.querySelector("#package-items").replaceChildren(); addItemRow(document.querySelector("#package-items")); }
    } catch (error) { toast(error.message, true); }
  });
  document.querySelector("#sale-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const body = formObject(event.currentTarget);
      body.items = readItemRows("sale-items");
      body.payments = [...document.querySelectorAll("#payment-rows .payment-row")].map((row) => ({
        method: row.querySelector('[name="method"]').value,
        amount: row.querySelector('[name="amount"]').value,
      }));
      const response = await submitForm(event.currentTarget, "/api/sales", () => body, (receipt) => `Venta registrada. Comprobante: ${receipt}.`);
      if (response) {
        document.querySelector("#sale-items").replaceChildren();
        document.querySelector("#payment-rows").replaceChildren();
        addItemRow(document.querySelector("#sale-items"));
        addPaymentRow();
        updateSaleTotal();
      }
    } catch (error) { toast(error.message, true); }
  });
  document.querySelector("#return-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = formObject(event.currentTarget);
    body.items = [{ sku: body.sku, quantity: Number(body.quantity) }];
    body.restock = event.currentTarget.elements.restock.checked;
    try {
      const result = await api("/api/returns", { method: "POST", body: JSON.stringify(body) });
      document.querySelector("#return-dialog").close();
      await refreshData();
      toast(`Devolución registrada por ${money(result.result)}.`);
    } catch (error) { toast(error.message, true); }
  });
  document.querySelector("#report-form").addEventListener("submit", (event) => {
    event.preventDefault();
    loadReports();
  });
  document.querySelector("#close-return").addEventListener("click", () => document.querySelector("#return-dialog").close());
  document.querySelector("#refresh-button").addEventListener("click", async () => {
    try { await refreshData(); toast("Datos actualizados."); } catch (error) { toast(error.message, true); }
  });
  document.querySelector("#product-search").addEventListener("input", renderCatalog);
  document.querySelector("#stock-filter").addEventListener("change", renderInventory);
  document.querySelector("#package-filter").addEventListener("change", renderPackages);
  document.querySelector('#sale-form [name="location_code"]').addEventListener("change", updateSaleTotal);
  document.querySelectorAll("[data-add-row]").forEach((button) => button.addEventListener("click", () => addItemRow(document.getElementById(button.dataset.addRow))));
  document.querySelector("[data-add-payment]").addEventListener("click", addPaymentRow);
  document.querySelector("#packages-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-package-action]");
    if (button) handlePackageAction(button);
  });
  document.querySelector("#sales-table").addEventListener("click", (event) => {
    const button = event.target.closest("[data-return]");
    if (button) openReturnDialog(button.dataset.return);
  });
  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.view)));
  document.querySelectorAll("[data-jump]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.jump)));
}

async function start() {
  const now = new Date();
  document.querySelector("#today-label").textContent = shortDate.format(now);
  document.querySelector("#current-time").textContent = `· ${shortDate.format(now)}`;
  const firstDay = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
  const today = now.toISOString().slice(0, 10);
  document.querySelector('#report-form [name="start"]').value = firstDay;
  document.querySelector('#report-form [name="end"]').value = today;
  bindForms();
  addItemRow(document.querySelector("#order-items"));
  addItemRow(document.querySelector("#package-items"));
  addItemRow(document.querySelector("#sale-items"));
  addPaymentRow();
  try {
    await refreshData();
    loadReports(firstDay, today);
  } catch (error) {
    toast(`No se pudo conectar con la aplicación: ${error.message}`, true);
  }
}

document.addEventListener("DOMContentLoaded", start);