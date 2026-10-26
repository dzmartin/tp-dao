const state = { data: null, user: null, currentView: "inicio", setupRequired: false, authLocations: [] };
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
  if (!response.ok) {
    const error = new Error(payload.error || "No se pudo completar la operación.");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function applyRoleAccess(user) {
  state.user = user;
  document.body.classList.add("authenticated");
  document.querySelector("#auth-screen").hidden = true;
  document.querySelector("#logout-button").hidden = false;
  const badge = document.querySelector("#profile-badge");
  badge.hidden = false;
  badge.textContent = user.role === "CD"
    ? `Centro de Distribución · ${user.username}`
    : `Local ${user.location_code} · ${user.username}`;
  document.querySelector("#dashboard-description").textContent = user.role === "CD"
    ? "Seguimiento de operación y abastecimiento de toda la red."
    : `Ventas, inventario y recepciones de ${user.location_name}.`;
  document.querySelector("#inventory-description").textContent = user.role === "CD"
    ? "Ajustes de stock del CD, solicitudes recibidas y conciliaciones."
    : "Consultá tu existencia, solicitá reposición y conciliá el inventario de tu sucursal.";
  document.querySelector("#logistics-eyebrow").textContent = user.role === "CD" ? "CENTRO DE DISTRIBUCIÓN" : "RECEPCIÓN LOCAL";
  document.querySelector("#logistics-title").textContent = user.role === "CD" ? "Logística interna" : "Recepción de mercadería";
  document.querySelector("#logistics-description").textContent = user.role === "CD"
    ? "Prepara, precinta y despacha bultos hacia los locales."
    : "Registrá la recepción y verificá cada bulto antes de liberar el stock.";
  document.querySelector("#metric-low-stock-note").textContent = user.role === "CD"
    ? "En todos los locales"
    : "En tu sucursal";
  document.querySelectorAll("[data-roles]").forEach((element) => {
    element.hidden = !element.dataset.roles.split(",").includes(user.role);
  });
}

function showAuthError(message) {
  const error = document.querySelector("#auth-error");
  error.textContent = message;
  error.hidden = false;
}

function updateRegistrationRoleFields() {
  const form = document.querySelector("#register-form");
  const role = form.querySelector('[name="role"]').value;
  const location = form.querySelector('[name="location_id"]');
  const isLocal = role === "LOCAL";
  document.querySelector("#register-location-field").hidden = !isLocal;
  location.disabled = !isLocal;
  location.required = isLocal;
  form.querySelector('[type="submit"]').disabled =
    state.setupRequired || (isLocal && location.options.length <= 1);
}

function updateSetupRoleFields(locations) {
  const form = document.querySelector("#setup-form");
  const role = form.querySelector('[name="role"]').value;
  const matchingLocations = locations.filter((location) => location.type === role);
  const locationField = document.querySelector("#setup-existing-location-field");
  const locationSelect = form.querySelector('[name="location_id"]');
  const newLocation = document.querySelector("#setup-new-location");
  const isLocal = role === "LOCAL";
  document.querySelector("#setup-location-label").textContent =
    isLocal ? "Sucursal" : "Centro de Distribución";
  locationField.hidden = !matchingLocations.length;
  newLocation.hidden = Boolean(matchingLocations.length);
  locationSelect.required = Boolean(matchingLocations.length);
  locationSelect.disabled = !matchingLocations.length;
  newLocation.querySelectorAll("input").forEach((input) => {
    input.disabled = Boolean(matchingLocations.length);
  });
  document.querySelector("#setup-location-title").textContent =
    isLocal ? "Nueva sucursal" : "Nuevo Centro de Distribución";
  form.querySelector('[name="location_code"]').placeholder = isLocal ? "LOC-01" : "CD";
  form.querySelector('[name="location_name"]').placeholder =
    isLocal ? "Local Centro" : "Centro de Distribución";
  setOptions(
    locationSelect,
    matchingLocations.map((location) => ({
      value: location.id,
      label: `${location.code} · ${location.name}`,
    })),
    isLocal ? "Elegir sucursal" : "Elegir Centro de Distribución",
  );
}

function showAuthScreen(setupStatus) {
  state.user = null;
  state.data = null;
  document.body.classList.remove("authenticated");
  document.querySelector("#auth-screen").hidden = false;
  document.querySelector("#logout-button").hidden = true;
  document.querySelector("#profile-badge").hidden = true;
  document.querySelector("#auth-error").hidden = true;
  const setup = setupStatus.setup_required;
  state.setupRequired = setup;
  state.authLocations = setupStatus.locations || [];
  document.querySelector("#login-panel").hidden = false;
  document.querySelector("#register-panel").hidden = true;
  document.querySelector("#setup-panel").hidden = true;
  const registrationLocations = (setupStatus.locations || []).filter((location) => location.type === "LOCAL");
  setOptions(
    document.querySelector('#register-form [name="location_id"]'),
    registrationLocations.map((location) => ({
      value: location.id,
      label: `${location.code} · ${location.name}`,
    })),
    "Elegir sucursal",
  );
  document.querySelector('#register-form [name="role"]').querySelector('option[value="LOCAL"]').disabled =
    !registrationLocations.length;
  updateRegistrationRoleFields();
  if (!setup) return;
  updateSetupRoleFields(setupStatus.locations || []);
}

async function enterApplication(user) {
  applyRoleAccess(user);
  await refreshData();
  document.querySelector("#price-list-items").replaceChildren();
  addPriceListRow(document.querySelector("#price-list-items"));
  const now = new Date();
  const firstDay = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
  loadReports(firstDay, now.toISOString().slice(0, 10));
}

async function initializeAuth() {
  const status = await api("/api/auth/status");
  if (!status.setup_required) {
    try {
      const session = await api("/api/auth/me");
      await enterApplication(session.user);
      return;
    } catch (error) {
      if (error.status !== 401) throw error;
    }
  }
  showAuthScreen(status);
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

function addOrderItemRow(container) {
  const row = document.createElement("div");
  row.className = "entry-row order-entry-row";
  const destination = document.createElement("select");
  destination.name = "destination_code";
  destination.className = "order-destination-select";
  destination.required = true;
  const product = document.createElement("select");
  product.name = "sku";
  product.required = true;
  for (const item of productOptions()) {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    product.append(option);
  }
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
  });
  row.append(destination, product, quantity, remove);
  container.append(row);
  if (state.data) populateSelects();
  return row;
}

function consolidatedOrderDestinations() {
  const byDestination = new Map();
  for (const row of document.querySelectorAll("#order-items .entry-row")) {
    const destination = row.querySelector('[name="destination_code"]').value;
    const sku = row.querySelector('[name="sku"]').value;
    const quantity = Number(row.querySelector('[name="quantity"]').value);
    if (!destination || !sku || !Number.isInteger(quantity) || quantity <= 0) {
      throw new Error("Completa el local, producto y una cantidad positiva en cada línea.");
    }
    const items = byDestination.get(destination) || {};
    items[sku] = (items[sku] || 0) + quantity;
    byDestination.set(destination, items);
  }
  return [...byDestination].map(([destination_code, items]) => ({ destination_code, items }));
}

function addPriceListRow(container) {
  const row = document.createElement("div");
  row.className = "price-list-item";

  const productField = document.createElement("label");
  productField.textContent = "Producto";
  const product = document.createElement("select");
  product.name = "sku";
  product.required = true;
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Elegir producto";
  product.append(placeholder);
  for (const item of state.data?.products || []) {
    const option = document.createElement("option");
    option.value = item.sku;
    option.textContent = `${item.sku} · ${item.name}`;
    product.append(option);
  }
  productField.append(product);

  const priceField = document.createElement("label");
  priceField.textContent = "Precio especial";
  const price = document.createElement("input");
  price.name = "price";
  price.type = "number";
  price.min = "0";
  price.step = "0.01";
  price.required = true;
  price.placeholder = "Ej.: 1250,50";
  priceField.append(price);

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-row";
  remove.textContent = "Quitar";
  remove.setAttribute("aria-label", "Quitar producto de la lista");
  remove.addEventListener("click", () => {
    row.remove();
    if (!container.children.length) addPriceListRow(container);
  });

  row.append(productField, priceField, remove);
  container.append(row);
  return row;
}

function addPaymentRow() {
  const container = document.querySelector("#payment-rows");
  const row = document.createElement("div");
  row.className = "payment-row";
  const method = document.createElement("select");
  method.name = "method";
  method.required = true;
  for (const [value, label] of [["efectivo", "Efectivo"], ["tarjeta", "Tarjeta"], ["transferencia", "Transferencia"], ["cuenta corriente", "Cuenta corriente"]]) {
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
  const hasCustomer = Boolean(document.querySelector("#sale-customer")?.value);
  rows.forEach((row, index) => {
    [...row.querySelector("select").options].forEach((option) => {
      option.disabled = (option.value === "cuenta corriente" && !hasCustomer)
        || (selected.includes(option.value) && option.value !== selected[index]);
    });
    if (!hasCustomer && row.querySelector('[name="method"]').value === "cuenta corriente") {
      row.querySelector('[name="method"]').value = "efectivo";
    }
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
  const priceListId = document.querySelector("#sale-price-list")?.value;
  const rows = [...document.querySelectorAll("#sale-items .entry-row")];
  const total = rows.reduce((sum, row) => {
    const sku = row.querySelector('[name="sku"]').value;
    const quantity = Number(row.querySelector('[name="quantity"]').value) || 0;
    const product = state.data?.products.find((item) => item.sku === sku);
    let price = product?.price_cents || 0;
    const listPrice = state.data?.price_list_items.find((item) => String(item.price_list_id) === String(priceListId) && item.sku === sku);
    if (listPrice) price = listPrice.price_cents;
    const today = new Date().toISOString().slice(0, 10);
    const promotions = (state.data?.promotions || []).filter((item) => item.starts_at <= today && item.ends_at >= today && (item.sku === sku || (!item.sku && item.category === product?.category)));
    for (const promotion of promotions) {
      const discount = promotion.kind === "percent" ? Math.floor(price * promotion.value / 10000) : promotion.value;
      price = Math.min(price, Math.max(0, (listPrice?.price_cents ?? product?.price_cents ?? 0) - discount));
    }
    return sum + price * quantity;
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
  const isLocal = state.user?.role === "LOCAL";
  const scopedLocations = isLocal ? locals : locations;
  const suppliers = state.data.suppliers;
  const categories = state.data.categories || [];
  const categorySelect = document.querySelector("#product-category");
  const categoryValue = categorySelect.value;
  setOptions(categorySelect, categories.map((item) => ({ value: item.name, label: item.name })), "Elegir categoría", categoryValue);
  const promotionCategory = document.querySelector("#promotion-category");
  const promotionCategoryValue = promotionCategory.value;
  setOptions(
    promotionCategory,
    categories.map((item) => ({ value: item.name, label: item.name })),
    "Sin categoría",
    promotionCategoryValue,
  );
  const reportLocation = document.querySelector('#report-form [name="location"]');
  const reportLocationValue = reportLocation.value;
  setOptions(
    reportLocation,
    locals.map((item) => ({ value: item.code, label: `${item.code} · ${item.name}` })),
    "Todos los locales",
    isLocal ? state.user.location_code : reportLocationValue,
  );
  const supplierSelect = document.querySelector("#product-supplier");
  const supplierValue = supplierSelect.value;
  setOptions(supplierSelect, suppliers.map((item) => ({ value: item.id, label: `${item.name} · ${item.tax_id}` })), "Sin proveedor", supplierValue);
  const customerSelect = document.querySelector("#sale-customer");
  const customerValue = customerSelect.value;
  setOptions(customerSelect, state.data.customers.map((item) => ({ value: item.id, label: `${item.name} · ${item.code}` })), "Venta sin cliente", customerValue);
  const priceListSelect = document.querySelector("#sale-price-list");
  const priceListValue = priceListSelect.value;
  setOptions(priceListSelect, state.data.price_lists.map((item) => ({ value: item.id, label: item.name })), "Precio base", priceListValue);
  const promotionSku = document.querySelector("#promotion-sku");
  const promotionSkuValue = promotionSku.value;
  setOptions(promotionSku, state.data.products.map((item) => ({ value: item.sku, label: `${item.sku} · ${item.name}` })), "Toda la categoría", promotionSkuValue);
  document.querySelectorAll(".location-select").forEach((select) => {
    const current = select.value;
    setOptions(
      select,
      scopedLocations.map((item) => ({ value: item.code, label: `${item.code} · ${item.name}` })),
      "Elegir ubicación",
      isLocal ? state.user.location_code : current,
    );
  });
  document.querySelectorAll(".product-select").forEach((select) => {
    const current = select.value;
    setOptions(select, productOptions(), "Elegir producto", current);
  });
  document.querySelectorAll(".local-select").forEach((select) => {
    const current = select.value;
    setOptions(
      select,
      locals.map((item) => ({ value: item.code, label: `${item.code} · ${item.name}` })),
      "Elegir local",
      isLocal ? state.user.location_code : current,
    );
  });
  const stockFilter = document.querySelector("#stock-filter");
  const currentFilter = stockFilter.value;
  setOptions(
    stockFilter,
    scopedLocations.map((item) => ({ value: item.code, label: `${item.code} · ${item.name}` })),
    "Todas las ubicaciones",
    isLocal ? state.user.location_code : currentFilter,
  );
  const orderSelect = document.querySelector("#package-order");
  const orderValue = orderSelect.value;
  const openOrders = state.data.orders.filter((order) => ["Abierta", "Preparando", "Con rechazo"].includes(order.status));
  setOptions(orderSelect, openOrders.map((item) => ({ value: item.id, label: `${item.batch_code ? `${item.batch_code} · ` : ""}${item.destination_code} · ${item.status}` })), "Elegir orden", orderValue);
  document.querySelectorAll("#order-items [name='sku'], #package-items .entry-row select").forEach((select) => {
    const current = select.value;
    setOptions(select, productOptions(), null, current);
  });
  document.querySelectorAll("#order-items .order-destination-select").forEach((select) => {
    const current = select.value;
    setOptions(
      select,
      locals.map((item) => ({ value: item.code, label: `${item.code} · ${item.name}` })),
      "Elegir local",
      isLocal ? state.user.location_code : current,
    );
    select.disabled = isLocal;
  });
  const userLocation = document.querySelector("#user-location");
  setOptions(
    userLocation,
    locals.map((item) => ({ value: item.id, label: `${item.code} · ${item.name}` })),
    "Elegir sucursal",
    userLocation.value,
  );
  const userRole = document.querySelector("#user-role");
  document.querySelector("#user-location-field").hidden = userRole.value !== "LOCAL";
  userLocation.disabled = userRole.value !== "LOCAL";
  userLocation.required = userRole.value === "LOCAL";
  document.querySelectorAll("#sale-items .entry-row select").forEach((select) => {
    const current = select.value;
    setOptions(select, productOptions(document.querySelector('#sale-form [name="location_code"]').value, true), null, current);
  });
  updateTraceFields();
  updateSaleTotal();
  syncPaymentOptions();
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
  document.querySelector("#products-table").innerHTML = products.map((item) => `<tr><td>${escapeHtml(item.sku)}</td><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.category)}</td><td>${escapeHtml(item.supplier_name || "—")}</td><td>${money(item.cost_cents)}</td><td>${money(item.price_cents)}</td><td>${item.minimum_stock}</td><td>${item.tracking_mode === "lot" ? "Lotes / FEFO + series" : item.tracking_mode === "serial" ? "Series" : "Sin trazabilidad adicional"}</td><td><button class="small-action" data-master-action="product-edit" data-id="${item.id}">Editar</button> <button class="small-action" data-master-action="product-delete" data-id="${item.id}">Eliminar</button></td></tr>`).join("") || emptyRow(9, "No hay productos que coincidan.");
  document.querySelector("#categories-table").innerHTML = (state.data.categories || []).map((category) => {
    const count = state.data.products.filter((product) => product.category_id === category.id).length;
    return `<tr><td>${escapeHtml(category.name)}</td><td>${count}</td><td><button class="small-action" data-category-action="rename" data-category-id="${category.id}">Renombrar</button> <button class="small-action" data-category-action="delete" data-category-id="${category.id}">Eliminar</button></td></tr>`;
  }).join("") || emptyRow(3, "Todavía no hay categorías.");
  document.querySelector("#locations-list").innerHTML = state.data.locations.map((item) => `<div class="location-chip"><b>${item.type === "CD" ? "CD" : escapeHtml(item.code.slice(0, 3))}</b><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.code)} · ${item.type === "CD" ? "Centro de Distribución" : "Local"}</small></span><span class="master-actions"><button class="small-action" data-master-action="location-edit" data-id="${item.id}">Editar</button>${item.type === "LOCAL" ? ` <button class="small-action" data-master-action="location-delete" data-id="${item.id}">Eliminar</button>` : ""}</span></div>`).join("") || '<p class="empty-cell">Agrega el primer CD o local.</p>';
  document.querySelector("#suppliers-table").innerHTML = state.data.suppliers.map((item) => `<tr><td>${escapeHtml(item.tax_id)}</td><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.phone || "—")}</td><td>${escapeHtml(item.email || "—")}</td><td><button class="small-action" data-master-action="supplier-edit" data-id="${item.id}">Editar</button> <button class="small-action" data-master-action="supplier-delete" data-id="${item.id}">Eliminar</button></td></tr>`).join("") || emptyRow(5, "Todavía no hay proveedores.");
}

function renderInventory() {
  const filter = document.querySelector("#stock-filter").value;
  const rows = state.data.stock.filter((item) => !filter || item.location_code === filter);
  document.querySelector("#stock-table").innerHTML = rows.map((item, index) => {
    const lots = state.data.lots.filter((lot) => lot.location_code === item.location_code && lot.sku === item.sku && lot.quantity > 0);
    const serials = state.data.serials.filter((serial) => serial.location_code === item.location_code && serial.sku === item.sku);
    const details = lots.length
      ? lots.map((lot) => ({
        label: `${lot.code}${lot.expiry_date ? ` · vence ${lot.expiry_date}` : ""}`,
        serials: serials.filter((serial) => serial.lot_code === lot.code),
      }))
      : [{ label: "Series del producto", serials }];
    const detailId = `serial-details-${index}`;
    const detailContent = details.map((detail) => `
      <div class="serial-detail-group">
        <strong>${escapeHtml(detail.label)}</strong>
        ${detail.serials.length
          ? `<ul>${detail.serials.map((serial) => `<li><code>${escapeHtml(serial.serial)}</code><span>${serial.status === "available" ? "Disponible" : serial.status === "sold" ? "Vendida" : serial.status === "retired" ? "Retirada" : escapeHtml(serial.status)}</span>${serial.lot_code ? `<span>Lote ${escapeHtml(serial.lot_code)}</span>` : ""}</li>`).join("")}</ul>`
          : "<small>No hay números de serie registrados para este lote.</small>"}
      </div>`).join("");
    const tracking = `<button class="small-action" type="button" data-serial-detail="${detailId}" aria-expanded="false">Ver detalle de lote</button><div class="serial-detail-panel" id="${detailId}" hidden>${detailContent}</div>`;
    return `<tr><td>${escapeHtml(item.location_code)}</td><td>${escapeHtml(item.sku)}</td><td>${escapeHtml(item.name)}</td><td class="${item.quantity <= item.minimum_stock ? "stock-low" : ""}">${item.quantity}</td><td>${item.minimum_stock}</td><td>${money(item.quantity * item.cost_cents)}</td><td>${tracking || "—"}</td></tr>`;
  }).join("") || emptyRow(7, "No hay existencias para mostrar.");
  document.querySelector("#orders-table").innerHTML = state.data.orders.map((order) => `<tr><td><strong>${escapeHtml(order.batch_code || order.code)}</strong><br><small>${escapeHtml(order.code)}</small></td><td>${escapeHtml(order.origin_code)}</td><td>${escapeHtml(order.destination_code)}</td><td>${order.lines.map((line) => `${escapeHtml(line.sku)} × ${line.quantity}`).join(", ")}</td><td><span class="status-pill" data-status="${escapeHtml(order.status)}">${escapeHtml(order.status)}</span></td><td>${shortDate.format(new Date(order.created_at))}</td></tr>`).join("") || emptyRow(6, "Todavía no hay órdenes de reposición.");
  renderInventoryCounts();
}

function renderPurchases() {
  const suggestions = state.data.purchase_suggestions;
  document.querySelector("#purchase-suggestions").innerHTML = suggestions.map((item) => `<tr><td>${escapeHtml(item.supplier_name)}</td><td>${escapeHtml(item.sku)}</td><td>${escapeHtml(item.name)}</td><td>${item.suggested_quantity}</td><td>${item.locations.map((location) => `${escapeHtml(location.code)}: ${location.shortage}`).join(", ")}</td><td><button class="small-action" data-purchase-supplier="${item.supplier_id}">Crear sugerida</button></td></tr>`).join("") || emptyRow(6, "No hay faltantes que requieran compra.");
  document.querySelector("#purchase-orders").innerHTML = state.data.purchase_orders.map((order) => `<tr><td>${escapeHtml(order.code)}</td><td>${escapeHtml(order.supplier_name)}</td><td>${order.lines.map((line) => `${escapeHtml(line.sku)} ${line.received_quantity}/${line.quantity}`).join(", ")}</td><td><span class="status-pill">${escapeHtml(order.status)}</span></td><td>${order.status === "Recibida" ? "" : `<button class="small-action" data-purchase-receive="${order.id}">Recibir</button>`}</td></tr>`).join("") || emptyRow(5, "Todavía no hay órdenes de compra.");
}

function renderCustomersAndPromotions() {
  document.querySelector("#customers-table").innerHTML = state.data.customers.map((item) => `<tr><td>${escapeHtml(item.code)}</td><td>${escapeHtml(item.name)}${item.phone ? `<br><small>${escapeHtml(item.phone)}</small>` : ""}</td><td>${money(item.balance_cents)}</td><td>${money(item.credit_limit_cents)}</td><td>${item.loyalty_points}</td></tr>`).join("") || emptyRow(5, "Todavía no hay clientes.");
  document.querySelector("#promotions-table").innerHTML = state.data.promotions.map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${item.kind === "percent" ? `${(item.value / 100).toLocaleString("es-AR")}%` : money(item.value)}</td><td>${escapeHtml(item.sku || item.category || "—")}</td><td>${escapeHtml(item.starts_at)} a ${escapeHtml(item.ends_at)}</td></tr>`).join("") || emptyRow(4, "No hay promociones activas.");
  document.querySelector("#price-lists-table").innerHTML = state.data.price_lists.map((list) => {
    const prices = state.data.price_list_items.filter((item) => item.price_list_id === list.id);
    return `<tr><td>${escapeHtml(list.name)}</td><td>${prices.length}</td><td>Para productos sin precio definido se usa el precio base.</td><td><button class="small-action" data-master-action="price-list-edit" data-id="${list.id}">Editar</button> <button class="small-action" data-master-action="price-list-delete" data-id="${list.id}">Eliminar</button></td></tr>`;
  }).join("") || emptyRow(3, "Todavía no hay listas de precios.");
}

function renderInventoryCounts() {
  const counts = state.data.inventory_counts.filter((item) => item.status === "Abierto");
  document.querySelector("#inventory-counts").innerHTML = counts.map((count) => `<form class="count-card" data-count-id="${count.id}"><div class="count-heading"><div><strong>${escapeHtml(count.code)}</strong><small>${escapeHtml(count.location_code)} · conteo abierto</small></div><button class="button button-dark" type="submit">Conciliar y cerrar</button></div><div class="table-wrap"><table><thead><tr><th>SKU / PRODUCTO</th><th>SISTEMA</th><th>CONTEO FÍSICO Y TRAZABILIDAD</th></tr></thead><tbody>${count.lines.map((line) => `<tr><td>${escapeHtml(line.sku)} · ${escapeHtml(line.name)}</td><td>${line.expected_quantity}</td><td class="count-input-cell"><input type="number" min="0" step="1" name="quantity:${escapeHtml(line.sku)}" value="${line.counted_quantity ?? ""}" required aria-label="Conteo de ${escapeHtml(line.name)}">${line.tracking_mode === "lot" ? `<input name="lots:${escapeHtml(line.sku)}" placeholder="Lote:AAAA-MM-DD:cantidad, otro lote..." aria-label="Lotes contados de ${escapeHtml(line.name)}">` : ""}${line.tracking_mode === "serial" ? `<textarea name="serials:${escapeHtml(line.sku)}" rows="2" placeholder="Una serie por línea" aria-label="Series contadas de ${escapeHtml(line.name)}"></textarea>` : ""}</td></tr>`).join("")}</tbody></table></div></form>`).join("") || '<p class="empty-cell">No hay conteos abiertos. Inicia uno para registrar existencias físicas.</p>';
  document.querySelector("#inventory-count-history").innerHTML = state.data.inventory_counts.map((count) => `<tr><td>${escapeHtml(count.code)}</td><td>${escapeHtml(count.location_code)}</td><td>${escapeHtml(count.status)}</td><td>${dateTime.format(new Date(count.created_at))}</td></tr>`).join("") || emptyRow(4, "Todavía no hay conteos registrados.");
}

async function createPurchaseSuggestion(supplierId) {
  const suggestions = state.data.purchase_suggestions.filter((item) => String(item.supplier_id) === String(supplierId));
  if (!suggestions.length) return;
  const supplier = suggestions[0].supplier_name;
  if (!window.confirm(`Crear orden sugerida para ${supplier}?`)) return;
  try {
    const items = Object.fromEntries(suggestions.map((item) => [item.sku, item.suggested_quantity]));
    await api("/api/purchase-orders", { method: "POST", body: JSON.stringify({ supplier_id: Number(supplierId), items }) });
    await refreshData();
    toast(`Orden de compra creada para ${supplier}.`);
  } catch (error) { toast(error.message, true); }
}

async function receivePurchaseOrder(orderId) {
  const order = state.data.purchase_orders.find((item) => item.id === Number(orderId));
  if (!order) return;
  const items = {};
  const trace = {};
  for (const line of order.lines) {
    const remaining = line.quantity - line.received_quantity;
    if (!remaining) continue;
    const value = window.prompt(`Cantidad recibida de ${line.sku} (pendiente: ${remaining}; 0 para omitir):`, String(remaining));
    if (value === null) return;
    const quantity = Number(value);
    if (!Number.isInteger(quantity) || quantity < 0 || quantity > remaining) {
      toast(`Cantidad inválida para ${line.sku}.`, true);
      return;
    }
    if (!quantity) continue;
    items[line.sku] = quantity;
    if (line.tracking_mode === "lot") {
      const lotCode = window.prompt(`Lote para ${line.sku}:`);
      if (lotCode === null) return;
      const expiryDate = window.prompt(`Vencimiento AAAA-MM-DD (opcional) para ${line.sku}:`, "");
      if (expiryDate === null) return;
      trace[line.sku] = { lot_code: lotCode, expiry_date: expiryDate || null };
    }
  }
  if (!Object.keys(items).length) return;
  try {
    await api(`/api/purchase-orders/${orderId}/receive`, { method: "POST", body: JSON.stringify({ items, trace }) });
    await refreshData();
    toast("Recepción de compra registrada en el CD.");
  } catch (error) { toast(error.message, true); }
}

function packageActionButtons(item) {
  if (state.user.role === "CD" && item.status === "Preparado") return `<button class="small-action" data-package-action="seal" data-code="${escapeHtml(item.code)}">Precintar</button>`;
  if (state.user.role === "CD" && item.status === "Precintado") return `<button class="small-action" data-package-action="dispatch" data-code="${escapeHtml(item.code)}">Despachar</button>`;
  if (state.user.role === "LOCAL" && item.status === "En tránsito") return `<button class="small-action" data-package-action="receive" data-code="${escapeHtml(item.code)}">Registrar recepción</button>`;
  if (state.user.role === "LOCAL" && item.status === "Recibido") return `<button class="small-action" data-package-action="verify" data-code="${escapeHtml(item.code)}">Verificar y liberar</button>`;
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

function renderUsers() {
  const users = state.data.users || [];
  document.querySelector("#users-table").innerHTML = users.map((user) => `<tr>
    <td>${escapeHtml(user.username)}</td>
    <td>${user.role === "CD" ? "Centro de Distribución" : "Local"}</td>
    <td>${escapeHtml(user.location_code || "—")}</td>
    <td>${user.active ? "Activa" : "Desactivada"}</td>
    <td>${user.active
      ? `<button class="small-action" data-user-action="reset" data-user-id="${user.id}">Cambiar contraseña</button>${user.id === state.data.user.id ? "" : ` <button class="small-action" data-user-action="delete" data-user-id="${user.id}">Desactivar</button>`}`
      : ""}</td>
  </tr>`).join("") || emptyRow(5, "Todavía no hay otras cuentas.");
}

function renderSales() {
  document.querySelector("#sales-table").innerHTML = state.data.sales.map((sale) => `<tr><td><span class="receipt-code">${escapeHtml(sale.receipt)}</span><br><small>${escapeHtml(sale.payment_methods || "")}</small></td><td>${escapeHtml(sale.location_code)}<br><small>${dateTime.format(new Date(sale.sold_at))}</small></td><td class="sale-items-summary">${sale.lines.map((line) => `${escapeHtml(line.name)} × ${line.quantity}`).join(", ")}</td><td><span class="status-pill" data-status="${escapeHtml(sale.state)}">${escapeHtml(sale.state)}</span></td><td><strong>${money(sale.total_cents)}</strong></td><td><a class="receipt-button" href="/api/sales/${encodeURIComponent(sale.receipt)}/receipt.pdf" download="comprobante-${escapeHtml(sale.receipt)}.pdf">Comprobante</a> <button class="return-button" data-return="${escapeHtml(sale.receipt)}" ${sale.state === "Devuelta" ? "disabled" : ""}>Devolver</button></td></tr>`).join("") || emptyRow(6, "Todavía no hay comprobantes.");
}

function showReceipt(receipt) {
  const sale = state.data.sales.find((item) => item.receipt === receipt);
  if (!sale) {
    toast(`No se encontró el comprobante ${receipt}.`, true);
    return;
  }
  const discountTotal = sale.lines.reduce((sum, line) => sum + line.discount_cents, 0);
  const subtotal = sale.total_cents + discountTotal;
  const lines = sale.lines.map((line) => `
    <tr>
      <td><strong>${escapeHtml(line.name)}</strong>${line.promotion_name ? `<small>${escapeHtml(line.promotion_name)}</small>` : ""}</td>
      <td>${line.quantity}</td>
      <td>${money(line.unit_price_cents)}</td>
      <td>${money(line.quantity * line.unit_price_cents)}</td>
    </tr>`).join("");
  const payments = sale.payments.map((payment) => `<li><span>${escapeHtml(payment.method)}</span><strong>${money(payment.amount_cents)}</strong></li>`).join("");
  document.querySelector("#receipt-paper").innerHTML = `
    <header class="receipt-heading">
      <div class="receipt-business">
        <strong>Faro | Operaciones comerciales</strong>
        <span>Sucursal ${escapeHtml(sale.location_name)} · ${escapeHtml(sale.location_code)}</span>
      </div>
      <div class="receipt-title-row">
        <h2 id="receipt-title">Comprobante de venta</h2>
        <strong class="receipt-number">N.º ${escapeHtml(sale.receipt)}</strong>
      </div>
      <p class="receipt-date">Fecha: ${dateTime.format(new Date(sale.sold_at))}</p>
      <div class="receipt-extra">
        ${sale.customer_name ? `<span>Cliente: ${escapeHtml(sale.customer_name)}</span>` : ""}
        ${sale.price_list_name ? `<span>Lista: ${escapeHtml(sale.price_list_name)}</span>` : ""}
      </div>
    </header>
    <div class="receipt-table-wrap"><table class="receipt-lines">
      <thead><tr><th>Producto</th><th>Cant.</th><th>Precio</th><th>Importe</th></tr></thead>
      <tbody>${lines}</tbody>
    </table></div>
    <section class="receipt-summary">
      <div><span>Subtotal</span><strong>${money(subtotal)}</strong></div>
      ${discountTotal ? `<div class="receipt-discount"><span>Descuentos aplicados</span><strong>−${money(discountTotal)}</strong></div>` : ""}
      <div class="receipt-grand-total"><span>Total</span><strong>${money(sale.total_cents)}</strong></div>
    </section>
    <section class="receipt-payments"><h3>Pagado con</h3><ul>${payments}</ul></section>
    <p class="receipt-thanks">Gracias por su compra</p>
    <footer class="receipt-actions"><button class="button button-dark" type="button" data-download-receipt="${escapeHtml(sale.receipt)}">Descargar PDF</button><button class="close-button" type="button" data-print-receipt>Imprimir</button><button class="close-button" type="button" data-close-receipt>Cerrar</button></footer>
  `;
  document.querySelector("#receipt-dialog").showModal();
}

function downloadReceiptPdf(receipt) {
  const link = document.createElement("a");
  link.href = `/api/sales/${encodeURIComponent(receipt)}/receipt.pdf`;
  link.download = `comprobante-${receipt}.pdf`;
  document.body.append(link);
  link.click();
  link.remove();
}

function renderAll() {
  populateSelects();
  renderDashboard();
  renderCatalog();
  renderInventory();
  renderPackages();
  renderSales();
  renderPurchases();
  renderCustomersAndPromotions();
  renderUsers();
}

async function refreshData() {
  try {
    state.data = await api("/api/bootstrap");
    renderAll();
    setConnectionStatus(true);
  } catch (error) {
    setConnectionStatus(false);
    if (error.status === 401) showAuthScreen({ setup_required: false, locations: [] });
    throw error;
  }
}

function setConnectionStatus(connected) {
  const label = document.querySelector("#connection-label");
  const dot = document.querySelector("#connection-dot");
  label.textContent = connected ? "Sistema conectado" : "Sin conexión";
  dot.classList.toggle("offline", !connected);
}

function navigate(view) {
  const requested = document.querySelector(`.nav-item[data-view="${view}"]`);
  if (!requested || requested.hidden) view = "inicio";
  state.currentView = view;
  document.querySelectorAll(".page-view").forEach((element) => element.classList.toggle("active", element.id === `view-${view}`));
  document.querySelectorAll(".nav-item").forEach((element) => {
    const active = element.dataset.view === view;
    element.classList.toggle("active", active);
    if (active) element.setAttribute("aria-current", "page");
    else element.removeAttribute("aria-current");
  });
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
    const query = new URLSearchParams({ start: values.start, end: values.end, location: values.location || "" });
    const report = await api(`/api/reports?${query}`);
    document.querySelector("#report-sales").innerHTML = report.sales.map((row) => `<tr><td>${escapeHtml(row.activity_date)}</td><td>${escapeHtml(row.location)}</td><td>${escapeHtml(row.payment_method)}</td><td>${money(row.net_cents)}</td></tr>`).join("") || emptyRow(4, "Sin ventas en este período.");
    document.querySelector("#report-products").innerHTML = report.best_sellers.map((row) => `<tr><td>${escapeHtml(row.sku)}<br><small>${escapeHtml(row.name)}</small></td><td>${row.net_units}</td><td>${money(row.net_revenue_cents)}</td><td>${money(row.margin_cents)}</td></tr>`).join("") || emptyRow(4, "Sin ventas en este período.");
    document.querySelector("#report-inventory").innerHTML = report.inventory.map((row) => `<tr><td>${escapeHtml(row.location)}</td><td>${escapeHtml(row.sku)}</td><td>${escapeHtml(row.name)}</td><td>${row.quantity}</td><td>${row.minimum_stock}</td><td>${money(row.valuation_cents)}</td><td>${row.at_minimum ? '<span class="status-pill" data-status="Rechazado">Reponer</span>' : '<span class="status-pill" data-status="Verificado">Normal</span>'}</td></tr>`).join("") || emptyRow(7, "No hay locales o productos.");
    const logistics = report.logistics;
    const average = logistics.average_transit_hours == null ? "—" : `${Number(logistics.average_transit_hours).toFixed(1)} h`;
    const wasteDetails = logistics.waste_movements.map((item) => `<p><b>${escapeHtml(item.waste_date)}</b> · ${escapeHtml(item.location)} · ${escapeHtml(item.sku)} · ${item.waste_quantity} u. · ${money(item.valuation_cents)}${item.cost_is_estimated ? " (estimado)" : ""}</p>`).join("") || "<small>Sin mermas registradas en este período.</small>";
    document.querySelector("#report-logistics").innerHTML = `<div class="logistic-stat"><strong>${logistics.rejected_count}</strong><small>Bultos rechazados</small></div><div class="logistic-stat"><strong>${logistics.verified_packages}</strong><small>Bultos verificados</small></div><div class="logistic-stat"><strong>${average}</strong><small>Tránsito promedio del período</small></div><div class="logistic-stat"><strong>${logistics.waste_quantity} u.</strong><small>Mermas · ${money(logistics.waste_valuation_cents)}</small></div><div class="rejection-list"><strong>Bultos rechazados</strong>${logistics.rejected_packages.map((item) => `<p><b>${escapeHtml(item.code)}</b> · ${escapeHtml(item.destination)} · ${escapeHtml(item.reason || "Rechazado")}</p>`).join("") || "<small>Sin rechazos registrados en este período.</small>"}<strong>Mermas registradas</strong>${wasteDetails}<small>Los bultos rechazados se informan aparte: su mercadería vuelve al CD y no se cuenta como merma.</small></div>`;
    document.querySelector("#report-movements").innerHTML = report.movements.map((row) => `<tr><td>${escapeHtml(row.movement_date)}</td><td>${escapeHtml(row.location)}</td><td>${escapeHtml(row.sku)}</td><td>${escapeHtml(row.movement_type)}${row.cost_is_estimated ? "<br><small>Valorización estimada</small>" : ""}</td><td>${row.quantity_delta > 0 ? "+" : ""}${row.quantity_delta}</td><td>${money(row.valuation_delta_cents)}</td></tr>`).join("") || emptyRow(6, "Sin movimientos en este período.");
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

async function handleMasterAction(button) {
  const id = Number(button.dataset.id);
  const action = button.dataset.masterAction;
  const ask = (label, current) => window.prompt(label, String(current ?? ""));
  const cancel = (value) => value === null;
  const send = async (path, body = {}) => api(path, { method: "POST", body: JSON.stringify(body) });

  try {
    if (action === "location-edit") {
      const item = state.data.locations.find((row) => row.id === id);
      if (!item) return;
      const code = ask("Código de ubicación:", item.code);
      if (cancel(code)) return;
      const name = ask("Nombre de ubicación:", item.name);
      if (cancel(name)) return;
      await send(`/api/locations/${id}/update`, { code, name, type: item.type });
    } else if (action === "location-delete") {
      const item = state.data.locations.find((row) => row.id === id);
      if (!item || !window.confirm(`¿Eliminar el local ${item.code}? Solo se puede si no tiene historial ni operaciones.`)) return;
      await send(`/api/locations/${id}/delete`);
    } else if (action === "supplier-edit") {
      const item = state.data.suppliers.find((row) => row.id === id);
      if (!item) return;
      const tax_id = ask("CUIT / identificador:", item.tax_id);
      if (cancel(tax_id)) return;
      const name = ask("Razón social:", item.name);
      if (cancel(name)) return;
      const phone = ask("Teléfono:", item.phone);
      if (cancel(phone)) return;
      const email = ask("Email:", item.email);
      if (cancel(email)) return;
      await send(`/api/suppliers/${id}/update`, { tax_id, name, phone, email });
    } else if (action === "supplier-delete") {
      const item = state.data.suppliers.find((row) => row.id === id);
      if (!item || !window.confirm(`¿Eliminar al proveedor ${item.name}? Solo se puede si no tiene productos u órdenes asociadas.`)) return;
      await send(`/api/suppliers/${id}/delete`);
    } else if (action === "product-edit") {
      const item = state.data.products.find((row) => row.id === id);
      if (!item) return;
      const sku = ask("SKU:", item.sku);
      if (cancel(sku)) return;
      const name = ask("Nombre del producto:", item.name);
      if (cancel(name)) return;
      const categoryOptions = state.data.categories.map((row) => row.name).join(", ");
      const category = ask(`Categoría (disponibles: ${categoryOptions}):`, item.category);
      if (cancel(category)) return;
      const cost = ask("Costo unitario:", (item.cost_cents / 100).toFixed(2));
      if (cancel(cost)) return;
      const price = ask("Precio de venta:", (item.price_cents / 100).toFixed(2));
      if (cancel(price)) return;
      const minimum_stock = ask("Stock mínimo:", item.minimum_stock);
      if (cancel(minimum_stock)) return;
      const supplierOptions = state.data.suppliers.map((row) => `${row.id}: ${row.name}`).join("\n");
      const supplier_id = ask(`ID del proveedor (vacío para ninguno):\n${supplierOptions}`, item.supplier_id || "");
      if (cancel(supplier_id)) return;
      if (supplier_id.trim() && !state.data.suppliers.some((row) => row.id === Number(supplier_id))) {
        throw new Error("El ID indicado no corresponde a un proveedor disponible.");
      }
      const tracking_mode = ask("Seguimiento: none, lot o serial", item.tracking_mode);
      if (cancel(tracking_mode)) return;
      await send(`/api/products/${id}/update`, {
        sku, name, category, cost, price, minimum_stock,
        supplier_id: supplier_id.trim() ? Number(supplier_id) : null,
        tracking_mode,
      });
    } else if (action === "product-delete") {
      const item = state.data.products.find((row) => row.id === id);
      if (!item || !window.confirm(`¿Eliminar ${item.sku} · ${item.name}? Los productos con stock, precios o historial están protegidos.`)) return;
      await send(`/api/products/${id}/delete`);
    } else if (action === "price-list-edit") {
      const list = state.data.price_lists.find((row) => row.id === id);
      if (!list) return;
      const name = ask("Nombre de la lista:", list.name);
      if (cancel(name)) return;
      const currentPrices = state.data.price_list_items
        .filter((row) => row.price_list_id === id)
        .map((row) => `${row.sku}:${(row.price_cents / 100).toFixed(2)}`).join(", ");
      const entries = ask("Precios SKU:importe separados por comas:", currentPrices);
      if (cancel(entries)) return;
      const prices = {};
      for (const entry of entries.split(",")) {
        const [sku, value] = entry.split(":").map((part) => part.trim());
        if (!sku || !value || Object.hasOwn(prices, sku)) throw new Error("Usa SKU:importe sin productos repetidos.");
        prices[sku] = value;
      }
      await send(`/api/price-lists/${id}/update`, { name, prices });
    } else if (action === "price-list-delete") {
      const list = state.data.price_lists.find((row) => row.id === id);
      if (!list || !window.confirm(`¿Eliminar la lista "${list.name}"? Las listas usadas en ventas están protegidas.`)) return;
      await send(`/api/price-lists/${id}/delete`);
    }
    await refreshData();
    toast("Cambios guardados.");
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
  document.querySelector("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const result = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify(formObject(form)),
      });
      form.reset();
      await enterApplication(result.user);
    } catch (error) {
      showAuthError(error.message);
    } finally {
      button.disabled = false;
    }
  });
  document.querySelector("#show-register").addEventListener("click", () => {
    document.querySelector("#login-panel").hidden = true;
    document.querySelector("#register-panel").hidden = state.setupRequired;
    document.querySelector("#setup-panel").hidden = !state.setupRequired;
    if (state.setupRequired) {
      updateSetupRoleFields(state.authLocations);
    }
    document.querySelector("#auth-error").hidden = true;
  });
  document.querySelector('#setup-form [name="role"]').addEventListener("change", () => {
    updateSetupRoleFields(state.authLocations);
  });
  document.querySelector("#show-login").addEventListener("click", () => {
    document.querySelector("#register-panel").hidden = true;
    document.querySelector("#login-panel").hidden = false;
    document.querySelector("#auth-error").hidden = true;
  });
  document.querySelector('#register-form [name="role"]').addEventListener("change", updateRegistrationRoleFields);
  document.querySelector("#register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const values = formObject(form);
      const result = await api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({
          username: values.username,
          password: values.password,
          role: values.role,
          location_id: values.role === "LOCAL" ? Number(values.location_id) : null,
        }),
      });
      form.reset();
      await enterApplication(result.user);
    } catch (error) {
      showAuthError(error.message);
    } finally {
      button.disabled = false;
    }
  });
  document.querySelector("#setup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = formObject(form);
    const body = {
      role: values.role,
      username: values.username,
      password: values.password,
    };
    if (document.querySelector("#setup-existing-location-field").hidden) {
      body.create_location = { code: values.location_code, name: values.location_name };
    } else {
      body.location_id = Number(values.location_id);
    }
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      await api("/api/auth/setup", { method: "POST", body: JSON.stringify(body) });
      const result = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: values.username, password: values.password }),
      });
      form.reset();
      await enterApplication(result.user);
    } catch (error) {
      showAuthError(error.message);
    } finally {
      button.disabled = false;
    }
  });
  document.querySelector("#logout-button").addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST", body: "{}" });
      showAuthScreen(await api("/api/auth/status"));
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.querySelector("#location-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/api/locations", undefined, "Ubicación creada.");
  });
  document.querySelector("#supplier-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/api/suppliers", undefined, "Proveedor creado.");
  });
  document.querySelector("#category-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/api/categories", undefined, "Categoría creada.");
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
      const destinations = consolidatedOrderDestinations();
      const response = await submitForm(
        event.currentTarget,
        "/api/replenishment-batches",
        () => ({ destinations }),
        (id) => `Orden consolidada creada. Identificador: ${id}.`,
      );
      if (response) { document.querySelector("#order-items").replaceChildren(); addOrderItemRow(document.querySelector("#order-items")); }
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
        showReceipt(response.result);
        downloadReceiptPdf(response.result);
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
  document.querySelector("#categories-table").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-category-action]");
    if (!button) return;
    const category = state.data.categories.find((item) => item.id === Number(button.dataset.categoryId));
    if (!category) return;
    try {
      if (button.dataset.categoryAction === "rename") {
        const name = window.prompt("Nuevo nombre para la categoría:", category.name);
        if (name === null || !name.trim()) return;
        await api(`/api/categories/${category.id}/rename`, { method: "POST", body: JSON.stringify({ name }) });
        await refreshData();
        toast("Categoría actualizada.");
      } else {
        if (!window.confirm(`¿Eliminar la categoría "${category.name}"?`)) return;
        await api(`/api/categories/${category.id}/delete`, { method: "POST", body: "{}" });
        await refreshData();
        toast("Categoría eliminada.");
      }
    } catch (error) { toast(error.message, true); }
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-master-action]");
    if (button) handleMasterAction(button);
  });
  document.querySelector("#stock-filter").addEventListener("change", renderInventory);
  document.querySelector("#stock-table").addEventListener("click", (event) => {
    const button = event.target.closest("[data-serial-detail]");
    if (!button) return;
    const panel = document.getElementById(button.dataset.serialDetail);
    if (!panel) return;
    panel.hidden = !panel.hidden;
    button.setAttribute("aria-expanded", String(!panel.hidden));
    button.textContent = panel.hidden ? "Ver detalle de lote" : "Ocultar detalle de lote";
  });
  document.querySelector("#package-filter").addEventListener("change", renderPackages);
  document.querySelector('#sale-form [name="location_code"]').addEventListener("change", updateSaleTotal);
  document.querySelector("#sale-price-list").addEventListener("change", updateSaleTotal);
  document.querySelector("#sale-customer").addEventListener("change", syncPaymentOptions);
  document.querySelector("#stock-form [name='sku']").addEventListener("change", updateTraceFields);
  document.querySelector("#refresh-purchases").addEventListener("click", async () => {
    try { await refreshData(); toast("Sugerencias actualizadas."); } catch (error) { toast(error.message, true); }
  });
  document.querySelector("#purchase-suggestions").addEventListener("click", (event) => {
    const button = event.target.closest("[data-purchase-supplier]");
    if (button) createPurchaseSuggestion(button.dataset.purchaseSupplier);
  });
  document.querySelector("#purchase-orders").addEventListener("click", (event) => {
    const button = event.target.closest("[data-purchase-receive]");
    if (button) receivePurchaseOrder(button.dataset.purchaseReceive);
  });
  document.querySelector("#user-role").addEventListener("change", () => {
    const role = document.querySelector("#user-role").value;
    const locationField = document.querySelector("#user-location-field");
    const location = document.querySelector("#user-location");
    locationField.hidden = role !== "LOCAL";
    location.disabled = role !== "LOCAL";
    location.required = role === "LOCAL";
  });
  document.querySelector("#user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = formObject(event.currentTarget);
    const body = {
      username: values.username,
      password: values.password,
      role: values.role,
      location_id: values.role === "LOCAL" ? Number(values.location_id) : null,
    };
    try {
      await api("/api/users", { method: "POST", body: JSON.stringify(body) });
      event.currentTarget.reset();
      populateSelects();
      await refreshData();
      toast("Cuenta creada.");
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.querySelector("#users-table").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-user-action]");
    if (!button) return;
    const userId = Number(button.dataset.userId);
    try {
      if (button.dataset.userAction === "reset") {
        const password = window.prompt("Nueva contraseña (mínimo 10 caracteres):");
        if (password === null) return;
        await api(`/api/users/${userId}/password`, {
          method: "POST",
          body: JSON.stringify({ password }),
        });
        toast(userId === state.data.user.id
          ? "Contraseña de tu cuenta actualizada."
          : "Contraseña actualizada; la cuenta deberá iniciar sesión nuevamente.");
      } else {
        if (!window.confirm("¿Desactivar esta cuenta?")) return;
        await api(`/api/users/${userId}/delete`, { method: "POST", body: "{}" });
        await refreshData();
        toast("Cuenta desactivada.");
      }
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.querySelector("#inventory-count-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/inventory-counts", { method: "POST", body: JSON.stringify(formObject(event.currentTarget)) });
      await refreshData();
      toast("Conteo físico iniciado.");
    } catch (error) { toast(error.message, true); }
  });
  document.querySelector("#inventory-counts").addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-count-id]");
    if (!form) return;
    event.preventDefault();
    try {
      const values = Object.fromEntries(new FormData(form));
      const quantities = {};
      const trace = {};
      for (const [key, value] of Object.entries(values)) {
        if (key.startsWith("quantity:")) {
          quantities[key.slice("quantity:".length)] = Number(value);
        } else if (key.startsWith("lots:")) {
          const sku = key.slice("lots:".length);
          trace[sku] = {
            lots: value.split(",").map((entry) => entry.trim()).filter(Boolean).map((entry) => {
              const [code, expiry_date, quantity] = entry.split(":").map((part) => part.trim());
              if (!code || quantity === undefined || !Number.isInteger(Number(quantity)) || Number(quantity) < 0) {
                throw new Error("Formato de lote inválido. Usa LOTE:AAAA-MM-DD:cantidad.");
              }
              return { code, expiry_date: expiry_date || null, quantity: Number(quantity) };
            }),
          };
        } else if (key.startsWith("serials:")) {
          trace[key.slice("serials:".length)] = {
            serials: value.split(/\r?\n/).map((serial) => serial.trim()).filter(Boolean),
          };
        }
      }
      await api(`/api/inventory-counts/${form.dataset.countId}/lines`, { method: "POST", body: JSON.stringify({ quantities, trace }) });
      await api(`/api/inventory-counts/${form.dataset.countId}/close`, { method: "POST", body: "{}" });
      await refreshData();
      toast("Inventario físico conciliado; diferencias registradas.");
    } catch (error) { toast(error.message, true); }
  });
  document.querySelectorAll("[data-add-row]").forEach((button) => button.addEventListener("click", () => {
    const container = document.getElementById(button.dataset.addRow);
    if (container.id === "order-items") addOrderItemRow(container);
    else addItemRow(container);
  }));
  document.querySelector("[data-add-payment]").addEventListener("click", addPaymentRow);
  document.querySelector("#packages-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-package-action]");
    if (button) handlePackageAction(button);
  });
  document.querySelector("#sales-table").addEventListener("click", (event) => {
    const returnButton = event.target.closest("[data-return]");
    if (returnButton) openReturnDialog(returnButton.dataset.return);
  });
  document.querySelector("#receipt-dialog").addEventListener("click", (event) => {
    const downloadButton = event.target.closest("[data-download-receipt]");
    if (downloadButton) downloadReceiptPdf(downloadButton.dataset.downloadReceipt);
    if (event.target.closest("[data-print-receipt]")) window.print();
    if (event.target.closest("[data-close-receipt]")) document.querySelector("#receipt-dialog").close();
  });
  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.view)));
  document.querySelectorAll("[data-jump]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.jump)));
  document.querySelector("#customer-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/api/customers", (body) => ({ ...body, credit_limit: Number(body.credit_limit) }), "Cliente creado.");
  });
  document.querySelector("#price-list-form").addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      const body = formObject(event.currentTarget);
      body.prices = {};
      for (const row of document.querySelectorAll("#price-list-items .price-list-item")) {
        const sku = row.querySelector('[name="sku"]').value;
        const price = Number(row.querySelector('[name="price"]').value);
        if (!sku || !Number.isFinite(price) || price < 0) {
          throw new Error("Elegí un producto e ingresá un precio válido para cada fila.");
        }
        if (Object.hasOwn(body.prices, sku)) {
          throw new Error(`El producto ${sku} está repetido en la lista.`);
        }
        body.prices[sku] = price;
      }
      if (!Object.keys(body.prices).length) throw new Error("Agregá al menos un producto a la lista.");
      submitForm(event.currentTarget, "/api/price-lists", () => body, "Lista de precios creada.");
    } catch (error) { toast(error.message, true); }
  });
  document.querySelector("#add-price-list-item").addEventListener("click", () => {
    addPriceListRow(document.querySelector("#price-list-items"));
  });
  document.querySelector("#promotion-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/api/promotions", (body) => ({ ...body, value: Number(body.value) }), "Promoción creada.");
  });
}

function updateTraceFields() {
  const sku = document.querySelector("#stock-form [name='sku']").value;
  const product = state.data?.products.find((item) => item.sku === sku);
  document.querySelector("#stock-lot-fields").hidden = product?.tracking_mode !== "lot";
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
  addOrderItemRow(document.querySelector("#order-items"));
  addItemRow(document.querySelector("#package-items"));
  addItemRow(document.querySelector("#sale-items"));
  addPaymentRow();
  try {
    await initializeAuth();
  } catch (error) {
    toast(`No se pudo conectar con la aplicación: ${error.message}`, true);
  }
}

document.addEventListener("DOMContentLoaded", start);