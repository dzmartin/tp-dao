const state = { data: null, toastTimer: null };
const byId = (id) => document.getElementById(id);
const localToday = new Date();
const today = `${localToday.getFullYear()}-${String(localToday.getMonth() + 1).padStart(2, "0")}-${String(localToday.getDate()).padStart(2, "0")}`;

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function money(cents) {
  return new Intl.NumberFormat("es-AR", {
    style: "currency", currency: "ARS", minimumFractionDigits: 2,
  }).format(Number(cents || 0) / 100);
}

function showToast(message, isError = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("visible"), 3800);
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "No se pudo completar la operación.");
  return result;
}

function setOptions(select, rows, label, value = "id", initialLabel = null) {
  const initial = initialLabel ? `<option value="">${escapeHTML(initialLabel)}</option>` : "";
  select.innerHTML = initial + rows.map((row) =>
    `<option value="${escapeHTML(row[value])}">${escapeHTML(label(row))}</option>`
  ).join("");
  select.disabled = rows.length === 0;
}

function locationsOfType(type) {
  return (state.data?.locations || []).filter((location) => location.type === type);
}

function productOptions() {
  return (state.data?.products || []).map((product) =>
    `<option value="${escapeHTML(product.sku)}">${escapeHTML(product.name)} · ${escapeHTML(product.sku)} · ${money(product.price_cents)}</option>`
  ).join("");
}

function addProductLine(containerId) {
  const container = byId(containerId);
  const line = document.createElement("div");
  line.className = "dynamic-line";
  line.innerHTML = `<select aria-label="Producto" required><option value="">Elegir producto</option>${productOptions()}</select>
    <input type="number" min="1" step="1" value="1" aria-label="Cantidad" required>
    <button class="remove-line" type="button" aria-label="Quitar producto" title="Quitar">×</button>`;
  line.querySelector("select").addEventListener("change", updateSaleSummary);
  line.querySelector("input").addEventListener("input", updateSaleSummary);
  line.querySelector(".remove-line").addEventListener("click", () => {
    line.remove();
    if (containerId === "sale-lines") updateSaleSummary();
  });
  container.append(line);
  if (containerId === "sale-lines") updateSaleSummary();
}

function updateSaleSummary() {
  const lines = [...byId("sale-lines").querySelectorAll(".dynamic-line")];
  let total = 0;
  const entries = [];
  for (const line of lines) {
    const sku = line.querySelector("select").value;
    const quantity = Number(line.querySelector("input").value);
    const product = state.data.products.find((item) => item.sku === sku);
    if (product && Number.isInteger(quantity) && quantity > 0) {
      const subtotal = product.price_cents * quantity;
      total += subtotal;
      entries.push(`<div class="summary-item"><span>${escapeHTML(product.name)} × ${quantity}</span><strong>${money(subtotal)}</strong></div>`);
    }
  }
  byId("sale-summary").innerHTML = entries.length
    ? entries.join("")
    : '<div class="empty-state">Agregá productos para ver el total.</div>';
  byId("sale-total").textContent = money(total);
}

function renderTable(rows, columns, emptyMessage = "Todavía no hay datos para mostrar.") {
  if (!rows.length) return `<div class="empty-state">${escapeHTML(emptyMessage)}</div>`;
  const heading = columns.map((column) => `<th>${escapeHTML(column.label)}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((column) => {
    const content = column.render
      ? column.render(row)
      : escapeHTML(column.value ? column.value(row) : row[column.key]);
    return `<td>${content}</td>`;
  }).join("")}</tr>`).join("");
  return `<table><thead><tr>${heading}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderDashboard() {
  const { sales_today: sales, products, inventory, packages, recent_sales: recent } = state.data;
  const isCenter = state.data.profile.type === "CD";
  const low = inventory.filter((item) => Number(item.existencia) <= Number(item.minimo));
  const inTransit = packages.filter((item) => item.status === "En tránsito").length;
  byId("profile-label").textContent = `${isCenter ? "Centro" : "Sucursal"} · ${state.data.profile.name}`;
  document.querySelector("#view-dashboard h1").innerHTML = isCenter
    ? 'Operación del centro <span class="wave">✳</span>'
    : `Sucursal ${escapeHTML(state.data.profile.name)} <span class="wave">✳</span>`;
  document.querySelector("#view-dashboard .page-heading .muted").textContent = isCenter
    ? "Administrá catálogo, existencias y envíos a las sucursales."
    : "Gestioná ventas, devoluciones, existencias y recepción de envíos.";
  byId("dashboard-metrics").innerHTML = [
    [isCenter ? "VENTAS DE LA RED" : "VENTAS DE HOY", money(sales.total), `${sales.count} operaciones registradas`, "↗", "green"],
    ["PRODUCTOS", products.length, "En el catálogo", "▤", "gold"],
    ["ALERTAS DE STOCK", low.length, "Existencias en el mínimo o debajo", "!", "blue"],
    ["EN TRÁNSITO", inTransit, "Bultos despachados", "⇄", "pink"],
  ].map(([label, value, caption, icon, color]) => `<article class="metric-card">
    <div class="metric-top"><span>${label}</span><span class="metric-icon ${color}">${icon}</span></div>
    <div class="metric-value">${value}</div><div class="metric-caption">${caption}</div>
  </article>`).join("");

  byId("recent-sales").innerHTML = recent.length
    ? `<div class="recent-list">${recent.map((sale) => `<div class="recent-row">
        <span class="sale-symbol">↗</span><span><span class="recent-primary">${escapeHTML(sale.receipt)} · ${escapeHTML(sale.store)}</span>
        <span class="recent-secondary">${escapeHTML(new Date(sale.sold_at).toLocaleString("es-AR"))}</span></span>
        <strong class="recent-amount">${money(sale.total_cents)}</strong></div>`).join("")}</div>`
    : '<div class="empty-state">Todavía no hay ventas. Cuando registres una, aparecerá acá.</div>';

  byId("low-stock-list").innerHTML = low.length
    ? `<div class="low-stock-grid">${low.slice(0, 6).map((item) => `<div class="low-stock-item">
        <strong>${escapeHTML(item.producto)}</strong><small>${escapeHTML(item.establecimiento)} · ${item.existencia} / ${item.minimo} unidades</small>
      </div>`).join("")}</div>`
    : '<div class="empty-state">Todo en orden: no hay productos bajo el mínimo.</div>';
}

function renderInventory() {
  const selected = byId("inventory-location").value;
  const rows = state.data.inventory.filter((item) =>
    selected === "all" || Number(item.establishment_id) === Number(selected)
  );
  const columns = [
    { label: "PRODUCTO", render: (row) => `<span class="table-primary">${escapeHTML(row.producto)}</span>` },
    { label: "SKU", key: "sku" },
    { label: "EXISTENCIA", value: (row) => `${row.existencia} u.` },
    { label: "MÍNIMO", render: (row) => `<span>${row.minimo} u.</span>${Number(row.establishment_id) === Number(state.data.profile.id)
      ? ` <button class="table-action" data-inventory-sku="${escapeHTML(row.sku)}" data-minimum="${escapeHTML(row.minimo)}">Editar</button>`
      : ""}` },
    { label: "ESTADO", render: (row) => Number(row.existencia) <= Number(row.minimo)
      ? '<span class="badge warning">Reponer</span>' : '<span class="badge">En nivel</span>' },
  ];
  if (state.data.profile.type === "CD") {
    columns.splice(2, 0, { label: "ESTABLECIMIENTO", key: "establecimiento" });
    columns.splice(5, 0, { label: "VALORIZACIÓN", value: (row) => money(row.valorizacion_centavos) });
  }
  byId("inventory-table").innerHTML = renderTable(rows, columns, "No hay productos en el catálogo.");
}

function packageActions(item) {
  const id = escapeHTML(item.id);
  if (state.data.profile.type === "CD" && Number(item.source_id) === Number(state.data.profile.id)) {
    if (item.status === "Preparado") return `<span class="action-group">
      <button class="table-action" data-package="${id}" data-action="precintar">Precintar</button>
      <button class="table-action reject" data-package="${id}" data-action="cancelar">Cancelar envío</button>
    </span>`;
    if (item.status === "Precintado") return `<span class="action-group">
      <button class="table-action" data-package="${id}" data-action="despachar">Despachar</button>
      <button class="table-action reject" data-package="${id}" data-action="cancelar">Cancelar envío</button>
    </span>`;
  }
  if (state.data.profile.type === "SUCURSAL" && item.status === "En tránsito") {
    return `<button class="table-action" data-package="${id}" data-action="recibir">Recibir y verificar</button>`;
  }
  if (state.data.profile.type === "SUCURSAL" && item.status === "Recibido") {
    return `<button class="table-action" data-package="${id}" data-action="verificar">Verificar recepción</button>`;
  }
  return `<span class="badge neutral">${escapeHTML(item.status)}</span>`;
}

function renderPackages() {
  const isCenter = state.data.profile.type === "CD";
  byId("view-logistics").querySelector(".page-heading .muted").textContent = isCenter
    ? "Prepará envíos consolidados y seguí su recepción en cada sucursal."
    : "Consultá los envíos destinados a esta sucursal y registrá su recepción.";
  byId("packages-card-title").textContent = isCenter
    ? "Envíos de la red"
    : "Envíos de mi sucursal";
  byId("packages-table").innerHTML = renderTable(state.data.packages, [
    { label: "REMITO", render: (row) => `<span class="table-primary">${escapeHTML(row.dispatch_note)}</span>` },
    { label: "CENTRO", key: "center" },
    { label: "SUCURSAL", key: "store" },
    { label: "PRECINTO", key: "seal" },
    { label: "ESTADO", render: (row) => `<span class="badge ${["Rechazado", "Cancelado"].includes(row.status) ? "danger" : row.status === "Verificado" ? "" : "warning"}">${escapeHTML(row.status)}</span>` },
    { label: "ACCIÓN", render: packageActions },
  ], "Aún no se prepararon bultos.");
}

function renderReplenishmentRequests() {
  const requests = state.data.replenishment_requests || [];
  if (state.data.profile.type === "CD") {
    const pending = requests.filter((item) => item.status === "Pendiente");
    byId("requests-table").innerHTML = pending.length
      ? `<table><thead><tr><th>SELECCIONAR</th><th>SUCURSAL Y CANTIDADES</th><th>REMITO</th><th>PRECINTO</th></tr></thead><tbody>${pending.map((request) => `
          <tr>
            <td><input class="fulfill-request-check" type="checkbox" value="${escapeHTML(request.id)}" aria-label="Seleccionar pedido de ${escapeHTML(request.store)}"></td>
            <td><span class="table-primary">${escapeHTML(request.store)}</span><small class="request-item-list">${request.items.map((item) => {
              const remaining = Number(item.remaining_quantity);
              const available = Number(item.available_quantity);
              const maximum = Math.min(remaining, available);
              return `${escapeHTML(item.name)} · ${remaining} u. pendientes (${item.fulfilled} de ${item.quantity} preparadas) · CD disponible: ${available} u.<br>
                <label class="fulfill-quantity-label">Cantidad a preparar
                  <input class="fulfill-quantity" type="number" min="0" max="${maximum}" step="1" value="${maximum}" data-request-id="${escapeHTML(request.id)}" data-sku="${escapeHTML(item.sku)}" aria-label="Cantidad a preparar de ${escapeHTML(item.name)} para ${escapeHTML(request.store)}" ${maximum === 0 ? "disabled" : ""}>
                </label>`;
            }).join("<br>")}</small></td>
            <td><input class="fulfill-remit" data-request-id="${escapeHTML(request.id)}" placeholder="REM-001" aria-label="Remito para ${escapeHTML(request.store)}"></td>
            <td><input class="fulfill-seal" data-request-id="${escapeHTML(request.id)}" placeholder="SELLO-001" aria-label="Precinto para ${escapeHTML(request.store)}"></td>
          </tr>`).join("")}</tbody></table>`
      : '<div class="empty-state">No hay pedidos pendientes de las sucursales.</div>';
  } else {
    const hasPending = requests.some((request) => request.status === "Pendiente");
    byId("store-request-note").textContent = hasPending
      ? "Ya hay un pedido pendiente. El centro debe prepararlo antes de que puedas enviar otro."
      : "";
    byId("store-request-note").hidden = !hasPending;
    byId("store-request-form").querySelectorAll("button, select, input").forEach((control) => {
      control.disabled = hasPending;
    });
    const rows = requests.map((request) => ({
      ...request,
      products: request.items.map((item) =>
        `${item.name} × ${item.quantity} (preparadas ${item.fulfilled}, pendientes ${item.remaining_quantity})`
      ).join(", "),
    }));
    byId("store-requests-table").innerHTML = renderTable(rows, [
      { label: "N° PEDIDO", value: (row) => `#${row.id}` },
      { label: "PRODUCTOS", key: "products" },
      { label: "FECHA", key: "created_at" },
      { label: "ESTADO", render: (row) => `<span class="badge ${row.status === "Pendiente" ? "warning" : ""}">${escapeHTML(row.status)}</span>` },
    ], "Todavía no solicitaste una reposición.");
  }
}

function renderCatalog() {
  byId("catalog-count").textContent = `${state.data.products.length} productos`;
  byId("supplier-count").textContent = `${state.data.suppliers.length} proveedores`;
  byId("catalog-table").innerHTML = renderTable(state.data.products, [
    { label: "PRODUCTO", render: (row) => `<span class="table-primary">${escapeHTML(row.name)}</span>` },
    { label: "SKU", key: "sku" },
    { label: "CATEGORÍA", key: "category" },
    { label: "COSTO", value: (row) => money(row.cost_cents) },
    { label: "PRECIO", value: (row) => money(row.price_cents) },
    { label: "PROVEEDOR", value: (row) => (row.supplier_ids || [])
      .map((id) => state.data.suppliers.find((supplier) => supplier.id === id)?.name)
      .filter(Boolean).join(", ") },
    { label: "ACCIÓN", render: (row) => `<button class="table-action" data-edit-product="${escapeHTML(row.sku)}">Editar</button>` },
  ], "Todavía no hay productos. Podés crear el primero desde este panel.");
  byId("supplier-table").innerHTML = renderTable(state.data.suppliers, [
    { label: "PROVEEDOR", render: (row) => `<span class="table-primary">${escapeHTML(row.name)}</span>` },
    { label: "CUIT / ID FISCAL", key: "tax_id" },
  ], "Todavía no hay proveedores registrados.");
}

function renderSalesHistory() {
  byId("sales-history-table").innerHTML = renderTable(state.data.sales, [
    { label: "COMPROBANTE", render: (row) => `<button class="table-action" data-view-receipt="${escapeHTML(row.id)}">${escapeHTML(row.receipt)} · PDF</button>` },
    { label: "SUCURSAL", key: "store" },
    { label: "FECHA", value: (row) => new Date(row.sold_at).toLocaleString("es-AR") },
    { label: "TOTAL", value: (row) => money(row.total_cents) },
    { label: "ESTADO", render: (row) => `<span class="badge ${row.status === "Devuelta" ? "neutral" : ""}">${escapeHTML(row.status)}</span>` },
    { label: "ACCIÓN", render: (row) => row.status === "Devuelta"
      ? '<span class="badge neutral">Sin saldo</span>'
      : `<button class="table-action" data-select-return="${escapeHTML(row.id)}">Devolver</button>` },
  ], "Aún no hay ventas para gestionar.");
}

function resetProductForm() {
  const form = byId("product-form");
  form.reset();
  form.elements.original_sku.value = "";
  form.elements.sku.readOnly = false;
  byId("product-submit").innerHTML = "Crear producto <span>→</span>";
  byId("product-cancel").hidden = true;
}

function editProduct(sku) {
  const product = state.data.products.find((item) => item.sku === sku);
  if (!product) return;
  const form = byId("product-form");
  form.elements.original_sku.value = product.sku;
  form.elements.sku.value = product.sku;
  form.elements.sku.readOnly = true;
  form.elements.name.value = product.name;
  form.elements.category.value = product.category;
  form.elements.cost.value = (product.cost_cents / 100).toFixed(2);
  form.elements.price.value = (product.price_cents / 100).toFixed(2);
  form.elements.supplier_id.value = product.supplier_ids?.[0] || "";
  byId("product-submit").innerHTML = "Guardar cambios <span>→</span>";
  byId("product-cancel").hidden = false;
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function loadReturn(saleId) {
  const details = await request(`/api/sales/${saleId}`);
  const { sale, items } = details;
  const returnable = items.filter((item) => Number(item.remaining_quantity) > 0);
  if (!returnable.length) {
    showToast("Esta venta ya no tiene unidades pendientes de devolución.", true);
    return;
  }
  byId("return-heading").textContent = `Devolución · ${sale.receipt}`;
  byId("return-form").elements.sale_id.value = sale.id;
  byId("return-lines").innerHTML = returnable.map((item) => `<div class="return-line" data-sale-line="${escapeHTML(item.line_id)}">
    <span class="return-product"><strong>${escapeHTML(item.name)}</strong><small>${escapeHTML(item.sku)} · ${money(item.unit_price_cents)} c/u · ${item.remaining_quantity} disponibles para devolver</small></span>
    <label>Cantidad<input class="return-quantity" type="number" min="0" max="${escapeHTML(item.remaining_quantity)}" value="0" step="1"></label>
    <label class="return-stock"><input class="return-restock" type="checkbox" checked> Reingresar al stock</label>
    <strong>${money(item.unit_price_cents * Number(item.remaining_quantity))}</strong>
  </div>`).join("");
  byId("return-card").hidden = false;
  byId("return-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function openReceiptPdf(saleId, receiptWindow = null) {
  const url = `/api/sales/${saleId}/receipt.pdf`;
  if (receiptWindow && !receiptWindow.closed) {
    receiptWindow.location.href = url;
    return true;
  }
  const openedWindow = window.open(url, "_blank");
  if (openedWindow) openedWindow.opener = null;
  return Boolean(openedWindow);
}

function renderReports(report) {
  byId("report-sales").innerHTML = renderTable(report.ventas_e_ingresos, [
    { label: "FECHA", key: "fecha" }, { label: "SUCURSAL", key: "sucursal" },
    { label: "MEDIO", key: "medio" },
    { label: "NETO", value: (row) => money(row.importe_neto_centavos) },
  ]);
  byId("report-products").innerHTML = renderTable(report.productos_y_margen, [
    { label: "SKU", key: "sku" }, { label: "PRODUCTO", key: "name" },
    { label: "UNIDADES", key: "unidades_vendidas" },
    { label: "INGRESOS", value: (row) => money(row.ingresos_centavos) },
    { label: "MARGEN", value: (row) => money(row.margen_estimado_centavos) },
  ]);
  byId("report-rejected").innerHTML = renderTable(report.bultos_rechazados, [
    { label: "REMITO", key: "remito" }, { label: "SUCURSAL", key: "sucursal" },
    { label: "FECHA", value: (row) => new Date(row.fecha_rechazo).toLocaleDateString("es-AR") },
    { label: "DETALLE", key: "detalle" },
  ]);
  byId("report-transit").innerHTML = renderTable(report.transito_promedio, [
    { label: "CENTRO", key: "centro" }, { label: "SUCURSAL", key: "sucursal" },
    { label: "BULTOS", key: "bultos" },
    { label: "PROMEDIO", value: (row) => `${Number(row.horas_promedio).toFixed(1)} h` },
  ]);
}

function populateSelectors() {
  const profile = state.data.profile;
  const isCenter = profile.type === "CD";
  const stores = locationsOfType("SUCURSAL");
  const centers = locationsOfType("CD");
  setOptions(byId("sale-store"), stores, (row) => `${row.name} · ${row.code}`, "id", "Elegir sucursal");
  if (!isCenter) {
    byId("sale-store").value = String(profile.id);
    byId("sale-store").disabled = true;
  }
  setOptions(byId("replenishment-center"), centers, (row) => `${row.name} · ${row.code}`, "id", "Elegir centro");
  if (isCenter) {
    byId("replenishment-center").value = String(profile.id);
    byId("replenishment-center").disabled = true;
  }
  setOptions(byId("adjustment-center"), centers, (row) => `${row.name} · ${row.code}`, "id", "Elegir centro");
  if (isCenter) {
    byId("adjustment-center").value = String(profile.id);
    byId("adjustment-center").disabled = true;
  }
  const locationSelect = byId("inventory-location");
  locationSelect.innerHTML = (isCenter ? '<option value="all">Todos los establecimientos</option>' : "") +
    state.data.locations.map((row) => `<option value="${escapeHTML(row.id)}">${escapeHTML(row.name)}</option>`).join("");
  locationSelect.value = isCenter ? "all" : String(profile.id);
  locationSelect.disabled = !isCenter;
  setOptions(byId("adjustment-sku"), state.data.products, (row) => `${row.name} · ${row.sku}`, "sku", "Elegir producto");
  setOptions(byId("product-supplier"), state.data.suppliers, (row) => `${row.name} · ${row.tax_id}`, "id", "Elegir proveedor");
}

function applyProfileVisibility(profile) {
  const activeView = document.querySelector(".view.active");
  const activeViewName = activeView?.id.replace("view-", "");
  document.querySelectorAll("[data-roles]").forEach((element) => {
    const roles = element.dataset.roles.split(/\s+/);
    element.hidden = !roles.includes(profile.type);
  });
  const allowedView = activeView
    && activeView.dataset.roles.split(/\s+/).includes(profile.type)
    ? activeViewName
    : "dashboard";
  goTo(allowedView);
}

function refreshProductOptions() {
  document.querySelectorAll(".dynamic-line select").forEach((select) => {
    const previous = select.value;
    select.innerHTML = `<option value="">Elegir producto</option>${productOptions()}`;
    select.value = previous;
  });
}

async function refresh() {
  const [bootstrap, sales] = await Promise.all([
    request("/api/bootstrap"),
    request("/api/sales"),
  ]);
  state.data = { ...bootstrap, sales };
  applyProfileVisibility(state.data.profile);
  if (state.data.profile.type !== "CD") {
    ["report-sales", "report-products", "report-rejected", "report-transit"].forEach((id) => {
      byId(id).replaceChildren();
    });
  }
  populateSelectors();
  refreshProductOptions();
  renderDashboard();
  renderInventory();
  renderCatalog();
  renderSalesHistory();
  renderPackages();
  if (!byId("sale-lines").children.length && state.data.products.length) addProductLine("sale-lines");
  if (state.data.profile.type === "SUCURSAL" && !byId("request-lines").children.length && state.data.products.length) addProductLine("request-lines");
  renderReplenishmentRequests();
  byId("loading").hidden = true;
  byId("profile-gate").hidden = true;
  byId("app-shell").hidden = false;
  byId("app-content").hidden = false;
}

async function showProfileGate() {
  const profiles = await request("/api/profiles");
  byId("app-shell").hidden = true;
  byId("profile-gate").hidden = false;
  byId("profile-error").hidden = true;
  byId("profile-options").innerHTML = profiles.map((profile) => {
    const role = profile.type === "CD" ? "Centro de distribución" : "Sucursal";
    const task = profile.type === "CD"
      ? "Catálogo, stock, envíos y reportes de la red."
      : "Ventas, devoluciones, stock local y recepción.";
    return `<button type="button" class="profile-option" data-profile-id="${escapeHTML(profile.id)}">
      <span class="profile-option-icon">${profile.type === "CD" ? "CD" : "LOC"}</span>
      <span><strong>${escapeHTML(profile.name)}</strong><small>${role} · ${escapeHTML(profile.code)}</small><small>${task}</small></span>
      <b>→</b>
    </button>`;
  }).join("");
  byId("first-center-form").hidden = profiles.length > 0;
  byId("profile-options").hidden = profiles.length === 0;
}

function showProfileError(message) {
  byId("app-shell").hidden = true;
  byId("profile-gate").hidden = false;
  byId("profile-options").hidden = true;
  byId("first-center-form").hidden = true;
  byId("profile-error").textContent = message;
  byId("profile-error").hidden = false;
}

async function startProfile(profileId) {
  await request("/api/session", {
    method: "POST",
    body: JSON.stringify({ profile_id: Number(profileId) }),
  });
  await refresh();
  if (state.data.profile.type === "CD") {
    loadReports().catch((error) => showToast(error.message, true));
  }
}

async function loadReports() {
  const form = byId("report-form");
  const data = new FormData(form);
  const query = new URLSearchParams({
    from_date: data.get("from_date"),
    to_date: data.get("to_date"),
  });
  renderReports(await request(`/api/reports?${query}`));
}

function goTo(view) {
  const section = byId(`view-${view}`);
  if (!section || section.hidden) return;
  document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
  document.querySelectorAll(".nav-link").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  const active = document.querySelector(`.nav-link[data-view="${view}"]`);
  byId("page-name").textContent = ({
    dashboard: "Resumen",
    sales: "Nueva venta",
    inventory: "Inventario",
    logistics: "Logística",
    catalog: "Catálogo",
    reports: "Reportes",
  })[view] || (active ? active.textContent.trim() : "Resumen");
  if (view === "reports") loadReports().catch((error) => showToast(error.message, true));
}

document.querySelectorAll(".nav-link").forEach((button) =>
  button.addEventListener("click", () => goTo(button.dataset.view))
);
document.querySelectorAll("[data-go]").forEach((button) =>
  button.addEventListener("click", () => goTo(button.dataset.go))
);
byId("add-sale-line").addEventListener("click", () => addProductLine("sale-lines"));
byId("add-request-line").addEventListener("click", () => addProductLine("request-lines"));
byId("inventory-location").addEventListener("change", renderInventory);
byId("catalog-table").addEventListener("click", (event) => {
  const button = event.target.closest("[data-edit-product]");
  if (button) editProduct(button.dataset.editProduct);
});
byId("product-cancel").addEventListener("click", resetProductForm);
byId("sales-history-table").addEventListener("click", (event) => {
  const receiptButton = event.target.closest("[data-view-receipt]");
  if (receiptButton) {
    if (!openReceiptPdf(receiptButton.dataset.viewReceipt)) {
      showToast("El navegador bloqueó la ventana del PDF. Permití ventanas emergentes para este sitio.", true);
    }
    return;
  }
  const returnButton = event.target.closest("[data-select-return]");
  if (returnButton) loadReturn(returnButton.dataset.selectReturn).catch((error) => showToast(error.message, true));
});
byId("report-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadReports().catch((error) => showToast(error.message, true));
});

byId("profile-options").addEventListener("click", (event) => {
  const option = event.target.closest("[data-profile-id]");
  if (!option) return;
  startProfile(option.dataset.profileId).catch((error) => showProfileError(error.message));
});

byId("first-center-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    const center = await request("/api/locations", {
      method: "POST",
      body: JSON.stringify({
        code: data.get("code"),
        name: data.get("name"),
        type: "CD",
      }),
    });
    await startProfile(center.id);
  } catch (error) {
    showProfileError(error.message);
  }
});

byId("change-profile").addEventListener("click", async () => {
  state.data = null;
  byId("app-shell").hidden = true;
  byId("profile-gate").hidden = false;
  byId("profile-options").hidden = true;
  byId("profile-error").hidden = true;
  try {
    await request("/api/session", { method: "DELETE" });
    await showProfileGate();
  } catch (error) {
    showProfileError(error.message);
  }
});

byId("sale-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const lines = [...byId("sale-lines").querySelectorAll(".dynamic-line")];
  const items = lines.map((line) => ({
    sku: line.querySelector("select").value,
    quantity: Number(line.querySelector("input").value),
  }));
  if (items.some((item) => !item.sku || !Number.isInteger(item.quantity) || item.quantity <= 0)) {
    showToast("Revisá los productos y las cantidades de la venta.", true);
    return;
  }
  const receiptWindow = window.open("about:blank", "_blank");
  if (receiptWindow) receiptWindow.opener = null;
  let saleRegistered = false;
  try {
    const result = await request("/api/sales", {
      method: "POST",
      body: JSON.stringify({
        store_id: Number(byId("sale-store").value),
        method: new FormData(form).get("method"),
        items,
      }),
    });
    saleRegistered = true;
    if (receiptWindow && !receiptWindow.closed) {
      receiptWindow.location.href = `/api/sales/${result.id}/receipt.pdf`;
    }
    form.reset();
    byId("sale-lines").innerHTML = "";
    addProductLine("sale-lines");
    await refresh();
    showToast(receiptWindow
      ? `Venta ${result.receipt} registrada · ${money(result.total_cents)}`
      : `Venta ${result.receipt} registrada · ${money(result.total_cents)}. Permití ventanas emergentes para abrir el PDF.`);
  } catch (error) {
    if (!saleRegistered && receiptWindow && !receiptWindow.closed) receiptWindow.close();
    showToast(error.message, true);
  }
});

byId("location-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  try {
    const result = await request("/api/locations", {
      method: "POST",
      body: JSON.stringify({
        code: data.get("code"),
        name: data.get("name"),
        type: data.get("type"),
      }),
    });
    showToast(`Establecimiento ${result.code} creado.`);
    form.reset();
    await refresh();
  } catch (error) {
    showToast(error.message, true);
  }
});

byId("supplier-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  try {
    const result = await request("/api/suppliers", {
      method: "POST",
      body: JSON.stringify({ tax_id: data.get("tax_id"), name: data.get("name") }),
    });
    showToast(`Proveedor ${result.tax_id} creado.`);
    form.reset();
    await refresh();
  } catch (error) {
    showToast(error.message, true);
  }
});

byId("product-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const cents = (value) => Math.round(Number(value) * 100);
  try {
    const originalSku = data.get("original_sku");
    const result = await request(
      originalSku ? `/api/products/${encodeURIComponent(originalSku)}` : "/api/products",
      {
      method: originalSku ? "PUT" : "POST",
      body: JSON.stringify({
        sku: data.get("sku"),
        name: data.get("name"),
        category: data.get("category"),
        cost_cents: cents(data.get("cost")),
        price_cents: cents(data.get("price")),
        supplier_id: Number(data.get("supplier_id")),
      }),
    });
    showToast(originalSku ? `Producto ${result.sku} actualizado.` : `Producto ${result.sku} creado.`);
    resetProductForm();
    await refresh();
  } catch (error) {
    showToast(error.message, true);
  }
});

byId("return-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const items = [...byId("return-lines").querySelectorAll(".return-line")].flatMap((line) => {
    const quantity = Number(line.querySelector(".return-quantity").value);
    if (!Number.isInteger(quantity) || quantity < 0) return [{ invalid: true }];
    if (quantity === 0) return [];
    return [{
      line_id: Number(line.dataset.saleLine),
      quantity,
      restock: line.querySelector(".return-restock").checked,
    }];
  });
  if (!items.length || items.some((item) => item.invalid)) {
    showToast("Indicá una cantidad válida mayor a cero para devolver.", true);
    return;
  }
  const data = new FormData(form);
  try {
    const result = await request(`/api/sales/${data.get("sale_id")}/returns`, {
      method: "POST",
      body: JSON.stringify({
        method: data.get("method"),
        items,
      }),
    });
    showToast(`Devolución registrada · reintegro ${money(result.amount_cents)}`);
    byId("return-card").hidden = true;
    byId("return-lines").innerHTML = "";
    await refresh();
  } catch (error) {
    showToast(error.message, true);
  }
});

byId("adjustment-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  try {
    await request("/api/stock-adjust", {
      method: "POST",
      body: JSON.stringify({
        center_id: Number(state.data.profile.id),
        sku: data.get("sku"),
        quantity_delta: Number(data.get("quantity_delta")),
        reference: data.get("reference"),
      }),
    });
    showToast("Ajuste de inventario registrado.");
    form.reset();
    await refresh();
  } catch (error) {
    showToast(error.message, true);
  }
});

byId("replenishment-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const selected = [...byId("requests-table").querySelectorAll(".fulfill-request-check:checked")];
  const orders = selected.map((checkbox) => {
    const requestId = checkbox.value;
    return {
      request_id: Number(requestId),
      remito: byId("requests-table").querySelector(`.fulfill-remit[data-request-id="${requestId}"]`).value.trim(),
      seal: byId("requests-table").querySelector(`.fulfill-seal[data-request-id="${requestId}"]`).value.trim(),
      items: [...byId("requests-table").querySelectorAll(`.fulfill-quantity[data-request-id="${requestId}"]`)]
        .map((input) => ({ sku: input.dataset.sku, quantity: Number(input.value) }))
        .filter((item) => Number.isInteger(item.quantity) && item.quantity > 0),
    };
  });
  if (!orders.length || orders.some((order) => !order.remito || !order.seal || !order.items.length)) {
    showToast("Seleccioná pedidos con al menos un producto y una cantidad a preparar; indicá también remito y precinto.", true);
    return;
  }
  try {
    const result = await request("/api/replenishments", {
      method: "POST",
      body: JSON.stringify({
        center_id: Number(state.data.profile.id),
        orders,
      }),
    });
    const remaining = result.packages.flatMap((item) =>
      item.remaining_items.map((product) => `${product.name} × ${product.quantity} u.`)
    );
    const pendingNote = remaining.length
      ? ` · Pendiente: ${remaining.join(", ")}`
      : "";
    showToast(`Reposición preparada · ${result.packages.length} sucursal(es) · lote #${result.batch_id}${pendingNote}`);
    await refresh();
  } catch (error) {
    showToast(error.message, true);
  }
});

byId("store-request-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const lines = [...byId("request-lines").querySelectorAll(".dynamic-line")];
  const items = lines.map((line) => ({
    sku: line.querySelector("select").value,
    quantity: Number(line.querySelector("input").value),
  }));
  if (!items.length || items.some((item) =>
    !item.sku || !Number.isInteger(item.quantity) || item.quantity <= 0
  )) {
    showToast("Elegí los productos y las cantidades que necesita la sucursal.", true);
    return;
  }
  try {
    const result = await request("/api/replenishment-requests", {
      method: "POST",
      body: JSON.stringify({ items }),
    });
    showToast(`Pedido #${result.id} enviado al centro.`);
    byId("request-lines").innerHTML = "";
    addProductLine("request-lines");
    await refresh();
  } catch (error) {
    showToast(error.message, true);
  }
});

byId("packages-table").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-package]");
  if (!button) return;
  const payload = { action: button.dataset.action };
  if (button.dataset.action === "cancelar" &&
      !window.confirm("¿Cancelar este envío? El pedido seguirá pendiente y podrás prepararlo nuevamente.")) return;
  try {
    const url = `/api/packages/${button.dataset.package}/action`;
    let result;
    if (button.dataset.action === "recibir") {
      await request(url, { method: "POST", body: JSON.stringify(payload) });
      payload.action = "verificar";
    }
    if (payload.action === "verificar") {
      payload.coincide_remito = window.confirm(
        "¿El contenido del bulto coincide con los productos y cantidades del remito?"
      );
      payload.intacto = window.confirm("¿El precinto está intacto?");
    }
    result = await request(url, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showToast(button.dataset.action === "cancelar"
      ? "Envío cancelado; la cantidad volvió al pedido pendiente."
      : result.message);
    await refresh();
  } catch (error) {
    showToast(error.message, true);
  }
});

byId("inventory-table").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-inventory-sku]");
  if (!button) return;
  const value = window.prompt(
    `Stock mínimo para ${button.dataset.inventorySku} en este establecimiento:`,
    button.dataset.minimum,
  );
  if (value === null) return;
  const minimum = Number(value);
  if (!Number.isInteger(minimum) || minimum < 0) {
    showToast("El stock mínimo debe ser un entero mayor o igual a cero.", true);
    return;
  }
  try {
    await request("/api/stock-minimum", {
      method: "POST",
      body: JSON.stringify({
        sku: button.dataset.inventorySku,
        minimum_stock: minimum,
      }),
    });
    showToast(`Stock mínimo de ${button.dataset.inventorySku} actualizado.`);
    await refresh();
  } catch (error) {
    showToast(error.message, true);
  }
});

byId("today-label").textContent = new Intl.DateTimeFormat("es-AR", {
  weekday: "short", day: "numeric", month: "short", year: "numeric",
}).format(new Date());
byId("report-form").elements.from_date.value = today;
byId("report-form").elements.to_date.value = today;
async function initialize() {
  try {
    const session = await request("/api/session");
    if (session.profile) {
      await refresh();
      if (state.data.profile.type === "CD") {
        loadReports().catch((error) => showToast(error.message, true));
      }
    } else {
      await showProfileGate();
    }
  } catch (error) {
    if (byId("app-shell").hidden) showProfileError(`No se pudo conectar: ${error.message}`);
    else showToast(error.message, true);
  }
}

initialize();
