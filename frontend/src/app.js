const state = { data: null, user: null, currentView: "inicio", setupRequired: false, authLocations: [], demoShortcuts: [], editingCategoryId: null };
const currency = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS", maximumFractionDigits: 2 });
const dateTime = new Intl.DateTimeFormat("es-AR", { dateStyle: "short", timeStyle: "short" });
const shortDate = new Intl.DateTimeFormat("es-AR", { dateStyle: "short" });
const paymentPriceFactors = {
  efectivo: { numerator: 4, denominator: 5 },
  tarjeta: { numerator: 11, denominator: 10 },
  transferencia: { numerator: 1, denominator: 1 },
};
const paymentMethodLabels = {
  efectivo: "Efectivo",
  tarjeta: "Tarjeta",
  transferencia: "Transferencia",
  pendiente: "Pendiente",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

function locationName(code) {
  return state.data?.locations?.find((location) => location.code === code)?.name || code || "—";
}

function productName(sku) {
  return state.data?.products?.find((product) => product.sku === sku)?.name || "Producto no disponible";
}

function money(cents) {
  return currency.format((Number(cents) || 0) / 100);
}

function roundedRatio(numerator, denominator) {
  return Math.floor((2 * numerator + denominator) / (2 * denominator));
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
  document.querySelector("#sidebar-user").hidden = false;
  document.querySelector("#logout-button").hidden = false;
  const roleLabel = user.role === "ADMIN"
    ? "Administrador"
    : user.role === "CD" ? "CD Central" : `Sucursal ${user.location_name}`;
  document.querySelector("#profile-avatar").textContent = user.username.slice(0, 1).toUpperCase();
  document.querySelector("#profile-name").textContent = user.username;
  document.querySelector("#profile-role").textContent = roleLabel;
  const badge = document.querySelector("#profile-badge");
  badge.hidden = false;
  badge.textContent = user.role === "ADMIN"
    ? `Administrador · ${user.username}`
    : user.role === "CD"
      ? `Centro de Distribución · ${user.username}`
      : `Local ${user.location_name} · ${user.username}`;
  document.querySelector("#dashboard-description").textContent = user.role === "ADMIN"
    ? "Resumen de ventas, inventario y actividad de la red."
    : user.role === "CD"
      ? "Seguimiento de envíos, inventario y reportes de la red."
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
  const isAdmin = role === "ADMIN";
  document.querySelector("#setup-location-label").textContent =
    isLocal ? "Sucursal" : "Centro de Distribución";
  locationField.hidden = isAdmin || !matchingLocations.length;
  newLocation.hidden = isAdmin || Boolean(matchingLocations.length);
  locationSelect.required = !isAdmin && Boolean(matchingLocations.length);
  locationSelect.disabled = isAdmin || !matchingLocations.length;
  newLocation.querySelectorAll("input").forEach((input) => {
    input.disabled = isAdmin || Boolean(matchingLocations.length);
    input.required = !isAdmin && !matchingLocations.length;
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
      label: location.name,
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
  document.querySelector("#sidebar-user").hidden = true;
  document.querySelector("#profile-badge").hidden = true;
  document.querySelector("#auth-error").hidden = true;
  const setup = setupStatus.setup_required;
  state.setupRequired = setup;
  state.authLocations = setupStatus.locations || [];
  state.demoShortcuts = setupStatus.demo_shortcuts || [];
  document.querySelector("#login-panel").hidden = false;
  document.querySelector("#register-panel").hidden = true;
  document.querySelector("#setup-panel").hidden = true;
  const demoAccess = document.querySelector("#demo-access");
  const demoButtons = document.querySelector("#demo-access-buttons");
  demoButtons.replaceChildren(...state.demoShortcuts.map((account) => {
    const button = document.createElement("button");
    button.className = "demo-login-button";
    button.type = "button";
    button.dataset.demoUsername = account.username;
    button.setAttribute("aria-label", `Ingresar como ${account.label} (${account.username})`);
    const label = document.createElement("strong");
    label.textContent = account.label;
    const username = document.createElement("small");
    username.textContent = account.username;
    button.append(label, username);
    return button;
  }));
  demoAccess.hidden = state.demoShortcuts.length === 0;
  const registrationLocations = (setupStatus.locations || []).filter((location) => location.type === "LOCAL");
  setOptions(
    document.querySelector('#register-form [name="location_id"]'),
    registrationLocations.map((location) => ({
      value: location.id,
      label: location.name,
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
  navigate("inicio");
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
    return { value: product.sku, label: `${product.name}${availability}`, disabled: sellableOnly && (!stock || stock.quantity < 1) };
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
    if (container.children.length > 1) {
      row.remove();
      updateMinimumStockSuggestion();
    }
    updateSaleTotal();
  });
  row.append(product, quantity, remove);
  container.append(row);
  row.addEventListener("input", updateSaleTotal);
  row.addEventListener("change", updateSaleTotal);
  return row;
}

function packageOrderLines(order) {
  const packedBySku = new Map();
  for (const packageItem of state.data.packages) {
    if (packageItem.status === "Rechazado") continue;
    const allocations = packageItem.order_lines || (
      packageItem.order_id === order.id
        ? packageItem.lines.map((line) => ({ ...line, order_id: packageItem.order_id }))
        : []
    );
    for (const line of allocations) {
      if (line.order_id !== order.id) continue;
      packedBySku.set(line.sku, (packedBySku.get(line.sku) || 0) + line.quantity);
    }
  }
  return order.lines.map((line) => {
    const prepared = packedBySku.get(line.sku) || 0;
    return {
      ...line,
      prepared_quantity: prepared,
      pending_quantity: Math.max(0, line.quantity - prepared),
    };
  });
}

function aggregatePackageOrderLines(destinationCode) {
  const totalsBySku = new Map();
  for (const order of state.data.orders) {
    if (
      order.destination_code !== destinationCode
      || !["Abierta", "Preparando", "Con rechazo"].includes(order.status)
    ) continue;
    const orderLines = packageOrderLines(order);
    if (!orderLines.some((line) => line.pending_quantity > 0)) continue;
    for (const line of orderLines) {
      const total = totalsBySku.get(line.sku) || {
        ...line,
        quantity: 0,
        prepared_quantity: 0,
        pending_quantity: 0,
      };
      total.quantity += line.quantity;
      total.prepared_quantity += line.prepared_quantity;
      total.pending_quantity += line.pending_quantity;
      totalsBySku.set(line.sku, total);
    }
  }
  return [...totalsBySku.values()].sort((first, second) => first.name.localeCompare(second.name, "es"));
}

function renderPackageOrderSummary(destinationCode) {
  const summary = document.querySelector("#package-order-summary");
  if (!destinationCode) {
    summary.hidden = true;
    summary.replaceChildren();
    return;
  }
  const aggregatedLines = aggregatePackageOrderLines(destinationCode);
  summary.innerHTML = `<p>Solicitud consolidada para <strong>${escapeHtml(locationName(destinationCode))}</strong></p>
    <p class="package-order-focus">El bulto cubrirá las órdenes abiertas de este local, empezando por la más antigua.</p>
    <ul>${aggregatedLines.map((line) => `<li>
      <span>${escapeHtml(line.name)}</span>
      <small>Solicitado ${line.quantity} · Preparado ${line.prepared_quantity} · Pendiente ${line.pending_quantity}</small>
    </li>`).join("")}</ul>`;
  summary.hidden = false;
}

function selectPackageDestination(destinationCode) {
  renderPackageOrderSummary(destinationCode);
  const container = document.querySelector("#package-items");
  container.replaceChildren();
  const remainingLines = destinationCode
    ? aggregatePackageOrderLines(destinationCode).filter((line) => line.pending_quantity > 0)
    : [];
  for (const line of remainingLines) {
    const row = addItemRow(container, line.sku);
    row.querySelector('[name="quantity"]').value = String(line.pending_quantity);
  }
  if (!remainingLines.length) addItemRow(container);
}

function addOrderItemRow(container, selectedSku = "", suggestedQuantity = 1) {
  const row = document.createElement("div");
  row.className = "entry-row order-entry-row";
  const destination = document.createElement("select");
  destination.name = "destination_code";
  destination.className = "order-destination-select";
  destination.required = true;
  const product = document.createElement("select");
  product.name = "sku";
  product.required = true;
  const productPlaceholder = document.createElement("option");
  productPlaceholder.value = "";
  productPlaceholder.textContent = "Elegir producto";
  product.append(productPlaceholder);
  for (const item of productOptions()) {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    product.append(option);
  }
  product.value = selectedSku;
  const quantity = document.createElement("input");
  quantity.name = "quantity";
  quantity.type = "number";
  quantity.min = "1";
  quantity.step = "1";
  quantity.value = String(suggestedQuantity);
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
  row.addEventListener("input", updateMinimumStockSuggestion);
  row.addEventListener("change", updateMinimumStockSuggestion);
  if (state.data) populateSelects();
  return row;
}

function minimumStockSuggestions() {
  const locationCode = state.user?.location_code;
  if (!locationCode || !state.data) return [];
  const requestedBySku = new Map();
  for (const row of document.querySelectorAll("#order-items .entry-row")) {
    const sku = row.querySelector('[name="sku"]').value;
    const quantity = Number(row.querySelector('[name="quantity"]').value);
    if (sku && Number.isInteger(quantity) && quantity > 0) {
      requestedBySku.set(sku, (requestedBySku.get(sku) || 0) + quantity);
    }
  }
  return state.data.stock
    .filter((item) => item.location_code === locationCode && item.quantity < item.minimum_stock)
    .map((item) => ({
      ...item,
      suggested_quantity: Math.max(
        0,
        item.minimum_stock - item.quantity - (requestedBySku.get(item.sku) || 0),
      ),
    }))
    .filter((item) => item.suggested_quantity > 0);
}

function updateMinimumStockSuggestion() {
  const button = document.querySelector("#add-minimum-stock-items");
  if (!button) return;
  const count = minimumStockSuggestions().length;
  button.disabled = count === 0;
  button.textContent = count
    ? `Agregar sugerencias de stock mínimo (${count})`
    : "No hay faltantes por agregar";
}

function addMinimumStockSuggestions() {
  const suggestions = minimumStockSuggestions();
  if (!suggestions.length) {
    updateMinimumStockSuggestion();
    return;
  }
  const container = document.querySelector("#order-items");
  for (const suggestion of suggestions) {
    const existingRow = [...container.querySelectorAll(".entry-row")]
      .find((row) => row.querySelector('[name="sku"]').value === suggestion.sku);
    if (existingRow) {
      const quantity = existingRow.querySelector('[name="quantity"]');
      quantity.value = String(Number(quantity.value) + suggestion.suggested_quantity);
    } else {
      const emptyRow = [...container.querySelectorAll(".entry-row")]
        .find((row) => !row.querySelector('[name="sku"]').value);
      if (emptyRow) {
        emptyRow.querySelector('[name="sku"]').value = suggestion.sku;
        emptyRow.querySelector('[name="quantity"]').value = String(suggestion.suggested_quantity);
      } else {
        addOrderItemRow(container, suggestion.sku, suggestion.suggested_quantity);
      }
    }
  }
  updateMinimumStockSuggestion();
  toast(`${suggestions.length} producto(s) agregado(s) para alcanzar el stock mínimo.`);
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
  amount.min = "0";
  amount.step = "0.01";
  amount.placeholder = "0,00";
  amount.dataset.autoAmount = "true";
  amount.setAttribute("aria-label", "Importe del pago");
  method.addEventListener("change", () => {
    amount.dataset.autoAmount = "true";
    amount.dataset.autoRemainder = "false";
    amount.setAttribute("aria-label", method.value === "efectivo" ? "Efectivo recibido" : `Importe de ${method.selectedOptions[0].textContent.toLowerCase()}`);
    updateSaleTotal(true, row === container.firstElementChild);
  });
  amount.addEventListener("input", () => {
    amount.dataset.autoAmount = "false";
    amount.dataset.autoRemainder = "false";
    const isFirstPayment = row === container.firstElementChild;
    updateSaleTotal(isFirstPayment, isFirstPayment);
  });
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-row";
  remove.textContent = "Quitar";
  remove.addEventListener("click", () => {
    if (container.children.length > 1) {
      if (row === container.firstElementChild && container.children.length === 2) {
        const remainingAmount = container.children[1].querySelector('[name="amount"]');
        if (remainingAmount.dataset.autoRemainder === "true") {
          remainingAmount.value = "";
          remainingAmount.dataset.autoRemainder = "false";
          remainingAmount.dataset.autoAmount = "true";
        }
      }
      row.remove();
      updateSaleTotal();
    }
  });
  row.append(method, amount, remove);
  container.append(row);
  return row;
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

function saleTotalCents() {
  const rows = [...document.querySelectorAll("#sale-items .entry-row")];
  return rows.reduce((sum, row) => {
    const sku = row.querySelector('[name="sku"]').value;
    const quantity = Number(row.querySelector('[name="quantity"]').value) || 0;
    const product = state.data?.products.find((item) => item.sku === sku);
    return sum + (product?.price_cents || 0) * quantity;
  }, 0);
}

function paymentChargeCents(method, baseCents) {
  const factor = paymentPriceFactors[method];
  return roundedRatio(baseCents * factor.numerator, factor.denominator);
}

function paymentBaseCents(method, chargeCents) {
  const factor = paymentPriceFactors[method];
  return roundedRatio(chargeCents * factor.denominator, factor.numerator);
}

function updateSaleTotal(recalculateRemainder = true, forceRemainder = false) {
  if (recalculateRemainder) {
    fillSinglePaymentAmount();
    fillSecondPaymentRemainder(forceRemainder);
  }
  const location = document.querySelector('#sale-form [name="location_code"]')?.value || "";
  const rows = [...document.querySelectorAll("#sale-items .entry-row")];
  const pricing = salePaymentPricing();
  document.querySelector("#sale-total").textContent = money(pricing.total);
  const breakdown = pricing.breakdown
    .map(([method, cents]) => `${paymentMethodLabels[method]}: ${money(cents)}`)
    .join(" · ");
  document.querySelector("#sale-payment-breakdown").textContent = breakdown
    ? `A cobrar: ${breakdown}`
    : "Ingresá los importes para ver cuánto corresponde a cada medio.";
  updateSaleChange(pricing.change);
  if (location) {
    rows.forEach((row) => {
      const select = row.querySelector('[name="sku"]');
      const selected = select.value;
      setOptions(select, productOptions(location), null, selected);
    });
  }
}

function fillSinglePaymentAmount() {
  const rows = [...document.querySelectorAll("#payment-rows .payment-row")];
  if (rows.length !== 1) return;

  const amount = rows[0].querySelector('[name="amount"]');
  if (amount.dataset.autoAmount !== "true") return;

  const method = rows[0].querySelector('[name="method"]').value;
  const cents = paymentChargeCents(method, saleTotalCents());
  amount.value = (cents / 100).toFixed(2);
}

function fillSecondPaymentRemainder(force = false) {
  const rows = [...document.querySelectorAll("#payment-rows .payment-row")];
  if (rows.length !== 2) return;

  const secondAmount = rows[1].querySelector('[name="amount"]');
  if (!force && secondAmount.dataset.autoAmount !== "true") return;

  const firstMethod = rows[0].querySelector('[name="method"]').value;
  const secondMethod = rows[1].querySelector('[name="method"]').value;
  const firstAmount = Math.round((Number(rows[0].querySelector('[name="amount"]').value) || 0) * 100);
  const remainder = firstMethod === secondMethod
    ? Math.max(0, paymentChargeCents(firstMethod, saleTotalCents()) - firstAmount)
    : paymentChargeCents(
      secondMethod,
      Math.max(0, saleTotalCents() - paymentBaseCents(firstMethod, firstAmount)),
    );
  secondAmount.value = (remainder / 100).toFixed(2);
  secondAmount.dataset.autoAmount = "true";
  secondAmount.dataset.autoRemainder = firstMethod !== secondMethod ? "true" : "false";
}

function salePaymentPricing() {
  const total = saleTotalCents();
  const amounts = [...document.querySelectorAll("#payment-rows .payment-row")].map((row) => ({
    method: row.querySelector('[name="method"]').value,
    cents: Math.round((Number(row.querySelector('[name="amount"]').value) || 0) * 100),
  }));
  const amountsByMethod = new Map();
  amounts.filter((payment) => payment.cents > 0).forEach((payment) => {
    amountsByMethod.set(payment.method, (amountsByMethod.get(payment.method) || 0) + payment.cents);
  });
  const enteredAmounts = [...amountsByMethod].map(([method, cents]) => ({ method, cents }));
  const methods = new Set(enteredAmounts.map((payment) => payment.method));
  const previewMethod = methods.size
    ? methods.values().next().value
    : amounts[0]?.method || "efectivo";
  const mixedMethods = methods.size > 1;
  const cashTendered = enteredAmounts.filter((payment) => payment.method === "efectivo")
    .reduce((sum, payment) => sum + payment.cents, 0);
  const otherAmounts = enteredAmounts.filter((payment) => payment.method !== "efectivo");
  const otherPayments = otherAmounts
    .reduce((sum, payment) => sum + payment.cents, 0);
  const otherBase = otherAmounts
    .reduce((sum, payment) => sum + paymentBaseCents(payment.method, payment.cents), 0);
  const remainingBase = Math.max(0, total - otherBase);
  const breakdown = new Map();
  let adjustedCashDue = 0;
  let adjustedTotal;
  let pending = 0;
  if (mixedMethods) {
    otherAmounts.forEach((payment) => {
      breakdown.set(payment.method, (breakdown.get(payment.method) || 0) + payment.cents);
    });
    if (methods.has("efectivo")) {
      adjustedCashDue = paymentChargeCents("efectivo", remainingBase);
      adjustedTotal = otherPayments + adjustedCashDue;
      if (adjustedCashDue > 0) breakdown.set("efectivo", adjustedCashDue);
      pending = Math.max(0, adjustedCashDue - cashTendered);
    } else {
      const remainderMethod = amounts[amounts.length - 1]?.method || previewMethod;
      pending = paymentChargeCents(remainderMethod, remainingBase);
      adjustedTotal = otherPayments + pending;
    }
  } else {
    adjustedTotal = paymentChargeCents(previewMethod, total);
    if (previewMethod === "efectivo") {
      adjustedCashDue = adjustedTotal;
      breakdown.set("efectivo", adjustedCashDue);
    } else {
      breakdown.set(previewMethod, adjustedTotal);
    }
  }
  if (pending > 0) breakdown.set("pendiente", pending);
  return {
    total: adjustedTotal,
    change: methods.has("efectivo") || previewMethod === "efectivo"
      ? Math.max(0, cashTendered - adjustedCashDue)
      : 0,
    breakdown: [...breakdown],
  };
}

function updateSaleChange(change) {
  const changeRow = document.querySelector("#sale-change");
  changeRow.hidden = change === 0;
  document.querySelector("#sale-change-amount").textContent = money(change);
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
  const reportLocation = document.querySelector('#report-form [name="location"]');
  const reportLocationValue = reportLocation.value;
  setOptions(
    reportLocation,
    locals.map((item) => ({ value: item.code, label: item.name })),
    "Todos los locales",
    isLocal ? state.user.location_code : reportLocationValue,
  );
  const supplierSelect = document.querySelector("#product-supplier");
  const supplierValue = supplierSelect.value;
  setOptions(supplierSelect, suppliers.map((item) => ({ value: item.id, label: `${item.name} · ${item.tax_id}` })), "Sin proveedor", supplierValue);
  document.querySelectorAll(".location-select").forEach((select) => {
    const current = select.value;
    setOptions(
      select,
      scopedLocations.map((item) => ({ value: item.code, label: item.name })),
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
      locals.map((item) => ({ value: item.code, label: item.name })),
      "Elegir local",
      isLocal ? state.user.location_code : current,
    );
  });
  const stockFilter = document.querySelector("#stock-filter");
  const currentFilter = stockFilter.value;
  setOptions(
    stockFilter,
    scopedLocations.map((item) => ({ value: item.code, label: item.name })),
    "Todas las ubicaciones",
    isLocal ? state.user.location_code : currentFilter,
  );
  const destinationSelect = document.querySelector("#package-destination");
  const destinationValue = destinationSelect.value;
  const openOrders = state.data.orders.filter((order) =>
    ["Abierta", "Preparando", "Con rechazo"].includes(order.status)
    && packageOrderLines(order).some((line) => line.pending_quantity > 0),
  );
  const openDestinations = [...new Set(openOrders.map((order) => order.destination_code))]
    .sort((first, second) => locationName(first).localeCompare(locationName(second), "es"));
  setOptions(
    destinationSelect,
    openDestinations.map((code) => ({ value: code, label: locationName(code) })),
    "Elegir local",
    destinationValue,
  );
  renderPackageOrderSummary(destinationSelect.value);
  document.querySelectorAll("#order-items [name='sku']").forEach((select) => {
    const current = select.value;
    setOptions(select, productOptions(), "Elegir producto", current);
  });
  document.querySelectorAll("#package-items .entry-row select").forEach((select) => {
    const current = select.value;
    setOptions(select, productOptions(), null, current);
  });
  document.querySelectorAll("#order-items .order-destination-select").forEach((select) => {
    const current = select.value;
    setOptions(
      select,
      locals.map((item) => ({ value: item.code, label: item.name })),
      "Elegir local",
      isLocal ? state.user.location_code : current,
    );
    select.disabled = isLocal;
  });
  const userLocation = document.querySelector("#user-location");
  setOptions(
    userLocation,
    locals.map((item) => ({ value: item.id, label: item.name })),
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
    return `<tr><td><strong>Bulto</strong><br><small>Remito</small></td><td>${escapeHtml(item.destination_name)}</td><td><span class="status-pill" data-status="${escapeHtml(item.status)}">${escapeHtml(item.status)}</span></td><td>${escapeHtml(event?.detail || "Sin eventos")}</td></tr>`;
  });
  document.querySelector("#dashboard-packages").innerHTML = packageRows.join("") || emptyRow(4, "Todavía no hay bultos en seguimiento.");
  const sales = state.data.sales.slice(0, 5).map((sale) => `<div class="recent-sale"><div><strong>Venta</strong><small>${escapeHtml(sale.location_name)} · ${dateTime.format(new Date(sale.sold_at))}</small></div><span class="recent-amount">${money(sale.total_cents)}</span></div>`);
  document.querySelector("#dashboard-sales").innerHTML = sales.join("") || '<p class="empty-cell">Todavía no hay ventas.</p>';
}

function renderCatalog() {
  const search = document.querySelector("#product-search").value.trim().toLowerCase();
  const products = state.data.products.filter((item) => `${item.sku} ${item.name} ${item.category}`.toLowerCase().includes(search));
  document.querySelector("#products-table").innerHTML = products.map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.category)}</td><td>${escapeHtml(item.supplier_name || "—")}</td><td>${money(item.cost_cents)}</td><td>${money(item.price_cents)}</td><td>${item.minimum_stock}</td><td><button class="small-action" data-master-action="product-edit" data-id="${item.id}">Editar</button> <button class="small-action" data-master-action="product-delete" data-id="${item.id}">Eliminar</button></td></tr>`).join("") || emptyRow(7, "No hay productos que coincidan.");
  document.querySelector("#categories-table").innerHTML = (state.data.categories || []).map((category) => {
    const count = state.data.products.filter((product) => product.category_id === category.id).length;
    if (state.editingCategoryId === category.id) {
      const formId = `category-edit-${category.id}`;
      return `<tr><td><form id="${formId}" class="category-edit-form" data-category-edit-form="${category.id}"><input name="name" value="${escapeHtml(category.name)}" required aria-label="Nuevo nombre para ${escapeHtml(category.name)}"></form></td><td>${count}</td><td><button class="small-action" type="submit" form="${formId}">Guardar</button> <button class="small-action" type="button" data-category-action="cancel" data-category-id="${category.id}">Cancelar</button></td></tr>`;
    }
    return `<tr><td>${escapeHtml(category.name)}</td><td>${count}</td><td><button class="small-action" data-category-action="rename" data-category-id="${category.id}">Renombrar</button> <button class="small-action" data-category-action="delete" data-category-id="${category.id}">Eliminar</button></td></tr>`;
  }).join("") || emptyRow(3, "Todavía no hay categorías.");
  document.querySelector("#locations-list").innerHTML = state.data.locations.map((item) => `<div class="location-chip"><b>${item.type === "CD" ? "CD" : escapeHtml(item.name.slice(0, 1).toUpperCase())}</b><span><strong>${escapeHtml(item.name)}</strong><small>${item.type === "CD" ? "Centro de Distribución" : "Local"}</small></span><span class="master-actions"><button class="small-action" data-master-action="location-edit" data-id="${item.id}">Editar</button>${item.type === "LOCAL" ? ` <button class="small-action" data-master-action="location-delete" data-id="${item.id}">Eliminar</button>` : ""}</span></div>`).join("") || '<p class="empty-cell">Agrega el primer CD o local.</p>';
  document.querySelector("#suppliers-table").innerHTML = state.data.suppliers.map((item) => `<tr><td>${escapeHtml(item.tax_id)}</td><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.phone || "—")}</td><td>${escapeHtml(item.email || "—")}</td><td><button class="small-action" data-master-action="supplier-edit" data-id="${item.id}">Editar</button> <button class="small-action" data-master-action="supplier-delete" data-id="${item.id}">Eliminar</button></td></tr>`).join("") || emptyRow(5, "Todavía no hay proveedores.");
}

function renderInventory() {
  const filter = document.querySelector("#stock-filter").value;
  const rows = state.data.stock.filter((item) => !filter || item.location_code === filter);
  document.querySelector("#stock-table").innerHTML = rows.map((item) => `<tr><td>${escapeHtml(locationName(item.location_code))}</td><td>${escapeHtml(item.name)}</td><td class="${item.quantity <= item.minimum_stock ? "stock-low" : ""}">${item.quantity}</td><td>${item.minimum_stock}</td><td>${money(item.quantity * item.cost_cents)}</td></tr>`).join("") || emptyRow(5, "No hay existencias para mostrar.");
  updateMinimumStockSuggestion();
  document.querySelector("#orders-table").innerHTML = state.data.orders.map((order) => `<tr><td><strong>${escapeHtml(order.batch_code || order.code)}</strong><br><small>${escapeHtml(order.code)}</small></td><td>${escapeHtml(locationName(order.origin_code))}</td><td>${escapeHtml(locationName(order.destination_code))}</td><td>${order.lines.map((line) => `${escapeHtml(line.name || productName(line.sku))} × ${line.quantity}`).join(", ")}</td><td><span class="status-pill" data-status="${escapeHtml(order.status)}">${escapeHtml(order.status)}</span></td><td>${shortDate.format(new Date(order.created_at))}</td></tr>`).join("") || emptyRow(6, "Todavía no hay órdenes de reposición.");
  renderInventoryCounts();
}

function renderPurchases() {
  const suggestions = state.data.purchase_suggestions;
  document.querySelector("#purchase-suggestions").innerHTML = suggestions.map((item) => `<tr><td>${escapeHtml(item.supplier_name)}</td><td>${escapeHtml(item.name)}</td><td>${item.suggested_quantity}</td><td>${item.locations.map((location) => `${escapeHtml(locationName(location.code))}: ${location.shortage}`).join(", ")}</td><td><button class="small-action" data-purchase-supplier="${item.supplier_id}">Crear sugerida</button></td></tr>`).join("") || emptyRow(5, "No hay faltantes que requieran compra.");
  document.querySelector("#purchase-orders").innerHTML = state.data.purchase_orders.map((order) => `<tr><td>${escapeHtml(order.code)}</td><td>${escapeHtml(order.supplier_name)}</td><td>${order.lines.map((line) => `${escapeHtml(line.name || productName(line.sku))} ${line.received_quantity}/${line.quantity}`).join(", ")}</td><td><span class="status-pill">${escapeHtml(order.status)}</span></td><td>${order.status === "Recibida" ? "" : `<button class="small-action" data-purchase-receive="${order.id}">Recibir</button>`}</td></tr>`).join("") || emptyRow(5, "Todavía no hay órdenes de compra.");
}

function renderInventoryCounts() {
  const counts = state.data.inventory_counts.filter((item) => item.status === "Abierto");
  document.querySelector("#inventory-counts").innerHTML = counts.map((count) => `<form class="count-card" data-count-id="${count.id}"><div class="count-heading"><div><strong>${escapeHtml(count.code)}</strong><small>${escapeHtml(locationName(count.location_code))} · conteo abierto</small></div><button class="button button-dark" type="submit">Conciliar y cerrar</button></div><p class="count-help">Ingresa el conteo físico solo de los productos que quieras conciliar. Los campos vacíos conservarán el stock actual.</p><div class="table-wrap"><table><thead><tr><th>PRODUCTO</th><th>SISTEMA</th><th>CONTEO FÍSICO</th></tr></thead><tbody>${count.lines.map((line) => `<tr><td>${escapeHtml(line.name)}</td><td>${line.expected_quantity}</td><td class="count-input-cell"><input type="number" min="0" step="1" name="quantity:${escapeHtml(line.sku)}" value="${line.counted_quantity ?? ""}" aria-label="Conteo de ${escapeHtml(line.name)}"></td></tr>`).join("")}</tbody></table></div></form>`).join("") || '<p class="empty-cell">No hay conteos abiertos. Inicia uno para registrar existencias físicas.</p>';
  document.querySelector("#inventory-count-history").innerHTML = state.data.inventory_counts.map((count) => `<tr><td>${escapeHtml(count.code)}</td><td>${escapeHtml(locationName(count.location_code))}</td><td>${escapeHtml(count.status)}</td><td>${dateTime.format(new Date(count.created_at))}</td></tr>`).join("") || emptyRow(4, "Todavía no hay conteos registrados.");
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
  for (const line of order.lines) {
    const remaining = line.quantity - line.received_quantity;
    if (!remaining) continue;
    const name = productName(line.sku);
    const value = window.prompt(`Cantidad recibida de ${name} (pendiente: ${remaining}; 0 para omitir):`, String(remaining));
    if (value === null) return;
    const quantity = Number(value);
    if (!Number.isInteger(quantity) || quantity < 0 || quantity > remaining) {
      toast(`Cantidad inválida para ${name}.`, true);
      return;
    }
    if (!quantity) continue;
    items[line.sku] = quantity;
  }
  if (!Object.keys(items).length) return;
  try {
    await api(`/api/purchase-orders/${orderId}/receive`, { method: "POST", body: JSON.stringify({ items }) });
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
  document.querySelector("#packages-list").innerHTML = packages.map((item) => {
    const lines = item.lines || [];
    const totalUnits = lines.reduce((total, line) => total + line.quantity, 0);
    const orderCodes = item.orders?.map((order) => order.code).join(", ") || item.order_code || "—";
    const transitEnd = item.received_at || (
      item.status === "En tránsito" ? new Date().toISOString() : null
    );
    const transitDuration = elapsedDuration(item.dispatched_at, transitEnd);
    const transitLabel = item.received_at
      ? transitDuration || "Duración no disponible"
      : item.dispatched_at
        ? transitDuration ? `${transitDuration} · en curso` : "Hora no disponible"
        : "Pendiente";
    return `<article class="package-card">
      <div class="package-identity"><strong>${escapeHtml(item.code)}</strong><small>Remito ${escapeHtml(item.dispatch_note)}</small><small>Órdenes: ${escapeHtml(orderCodes)}</small></div>
      <div class="package-destination"><strong>${escapeHtml(item.destination_name)}</strong><small>Precinto ${escapeHtml(item.seal || "Pendiente")}</small></div>
      <div><span class="status-pill" data-status="${escapeHtml(item.status)}">${escapeHtml(item.status)}</span><div class="package-lines">${lines.map((line) => `${escapeHtml(line.name || productName(line.sku))} × ${line.quantity}`).join(" · ")}</div></div>
      <div class="package-actions">${packageActionButtons(item)}</div>
      <div class="package-summary"><span><small>Artículos</small><strong>${lines.length}</strong></span><span><small>Unidades totales</small><strong>${totalUnits}</strong></span><span><small>Tiempo de tránsito</small><strong>${escapeHtml(transitLabel)}</strong></span></div>
      ${packageTimeline(item)}
      <details class="events-detail"><summary>Referencias del bulto</summary><p><strong>Bulto:</strong> ${escapeHtml(item.code)} · <strong>Remito:</strong> ${escapeHtml(item.dispatch_note)} · <strong>Órdenes:</strong> ${escapeHtml(orderCodes)}</p></details>
    </article>`;
  }).join("") || '<p class="empty-cell">No hay bultos para este filtro.</p>';
}

function renderRejectedPackages() {
  const packages = state.data.packages.filter((item) => item.status === "Rechazado");
  document.querySelector("#rejected-packages-list").innerHTML = packages.map((item) => {
    const reason = item.events.filter((event) => event.state === "Rechazado").at(-1)?.detail || "Rechazado por el local.";
    const lines = item.lines.map((line) => `<li><span>${escapeHtml(line.name || productName(line.sku))}</span><strong>${line.quantity} u.</strong></li>`).join("");
    const resolution = item.disposition === "stock"
      ? '<span class="rejected-resolution rejected-resolution-stock">Devuelto al stock del CD</span>'
      : item.disposition === "discard"
        ? '<span class="rejected-resolution rejected-resolution-discard">Descartado · merma registrada</span>'
        : `<div class="rejected-package-actions"><button class="small-action rejected-stock-action" data-rejected-disposition="stock" data-package-code="${escapeHtml(item.code)}" type="button">Devolver al stock</button><button class="small-action danger" data-rejected-disposition="discard" data-package-code="${escapeHtml(item.code)}" type="button">Descartar</button></div>`;
    return `<article class="rejected-package-card">
      <header class="rejected-package-heading"><div><strong>${escapeHtml(item.code)}</strong><small>Remito ${escapeHtml(item.dispatch_note)} · ${escapeHtml(item.destination_name)}</small></div><span class="status-pill" data-status="Rechazado">Rechazado</span></header>
      <p class="rejected-package-reason">${escapeHtml(reason)}</p>
      <ul class="rejected-package-lines">${lines}</ul>
      <footer class="rejected-package-footer">${resolution}</footer>
    </article>`;
  }).join("") || '<p class="empty-cell">Todavía no hay bultos rechazados por los locales.</p>';
}

function packageTimeline(item) {
  const steps = item.status === "Rechazado"
    ? ["Preparado", "Precintado", "En tránsito", "Recibido", "Rechazado"]
    : ["Preparado", "Precintado", "En tránsito", "Recibido", "Verificado"];
  const currentIndex = steps.indexOf(item.status);
  const eventsByState = new Map(item.events.map((event) => [event.state, event]));
  return `<ol class="package-timeline" aria-label="Historial de estados del bulto">${steps.map((step, index) => {
    const event = eventsByState.get(step);
    const previousEvent = index > 0 ? eventsByState.get(steps[index - 1]) : null;
    const elapsed = event && previousEvent
      ? elapsedDuration(previousEvent.occurred_at, event.occurred_at)
      : null;
    const isCurrent = index === currentIndex;
    const className = step === "Rechazado" && isCurrent
      ? "timeline-step timeline-rejected"
      : `timeline-step ${index < currentIndex ? "timeline-completed" : isCurrent ? "timeline-current" : "timeline-pending"}`;
    return `<li class="${className}"><strong>${escapeHtml(step)}</strong>${event ? `<p>${escapeHtml(event.detail)}</p><time datetime="${escapeHtml(event.occurred_at)}">${dateTime.format(new Date(event.occurred_at))}</time>${elapsed ? `<small class="timeline-duration">${escapeHtml(elapsed)} desde el estado anterior</small>` : ""}` : ""}</li>`;
  }).join("")}</ol>`;
}

function elapsedDuration(startAt, endAt) {
  if (!startAt || !endAt) return null;
  const start = Date.parse(startAt);
  const end = Date.parse(endAt);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;

  const totalMinutes = Math.floor((end - start) / 60000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  return [
    days ? `${days} d` : "",
    hours ? `${hours} h` : "",
    minutes || (!days && !hours) ? `${minutes} min` : "",
  ].filter(Boolean).join(" ");
}

function renderUsers() {
  const users = state.data.users || [];
  document.querySelector("#users-table").innerHTML = users.map((user) => `<tr>
    <td>${escapeHtml(user.username)}</td>
    <td>${user.role === "ADMIN" ? "Administrador" : user.role === "CD" ? "Centro de Distribución" : "Local"}</td>
    <td>${escapeHtml(locationName(user.location_code))}</td>
    <td>${user.active ? "Activa" : "Desactivada"}</td>
    <td>${user.active
      ? `<button class="small-action" data-user-action="reset" data-user-id="${user.id}">Cambiar contraseña</button>${user.id === state.data.user.id ? "" : ` <button class="small-action" data-user-action="delete" data-user-id="${user.id}">Desactivar</button>`}`
      : ""}</td>
  </tr>`).join("") || emptyRow(5, "Todavía no hay otras cuentas.");
}

function renderSales() {
  document.querySelector("#sales-table").innerHTML = state.data.sales.map((sale) => `<tr><td><span class="receipt-code">${escapeHtml(sale.receipt)}</span><br><small>${escapeHtml(sale.payment_methods || "")}</small></td><td>${escapeHtml(sale.location_name)}<br><small>${dateTime.format(new Date(sale.sold_at))}</small></td><td class="sale-items-summary">${sale.lines.map((line) => `${escapeHtml(line.name)} × ${line.quantity}`).join(", ")}</td><td><span class="status-pill" data-status="${escapeHtml(sale.state)}">${escapeHtml(sale.state)}</span></td><td><strong>${money(sale.total_cents)}</strong></td><td><a class="receipt-button" href="/api/sales/${encodeURIComponent(sale.receipt)}/receipt.pdf" download="comprobante-${escapeHtml(sale.receipt)}.pdf">Comprobante</a> <button class="return-button" data-return="${escapeHtml(sale.receipt)}" ${sale.state === "Devuelta" ? "disabled" : ""}>Devolver</button></td></tr>`).join("") || emptyRow(6, "Todavía no hay comprobantes.");
}

function showReceipt(receipt) {
  const sale = state.data.sales.find((item) => item.receipt === receipt);
  if (!sale) {
    toast(`No se encontró el comprobante ${receipt}.`, true);
    return;
  }
  const lines = sale.lines.map((line) => `
    <tr>
      <td><strong>${escapeHtml(line.name)}</strong></td>
      <td>${line.quantity}</td>
      <td>${money(line.unit_price_cents)}</td>
      <td>${money(line.quantity * line.unit_price_cents)}</td>
    </tr>`).join("");
  const subtotal = sale.lines.reduce((sum, line) => sum + line.quantity * line.unit_price_cents, 0);
  const adjustment = sale.total_cents - subtotal;
  const adjustmentLabel = adjustment < 0 ? "Descuento por medio de pago" : "Recargo por medio de pago";
  const adjustmentLine = adjustment
    ? `<div class="receipt-adjustment"><span>${adjustmentLabel}</span><strong>${money(adjustment)}</strong></div>`
    : "";
  const payments = sale.payments.map((payment) => `<li><span>${escapeHtml(payment.method)}</span><strong>${money(payment.amount_cents)}</strong></li>`).join("");
  document.querySelector("#receipt-paper").innerHTML = `
    <header class="receipt-heading">
      <div class="receipt-business">
        <strong>La pulga mercado</strong>
        <span>${escapeHtml(sale.location_name)}</span>
      </div>
      <div class="receipt-title-row">
        <h2 id="receipt-title">Comprobante de venta</h2>
        <strong class="receipt-number">N.º ${escapeHtml(sale.receipt)}</strong>
      </div>
      <p class="receipt-date">Fecha: ${dateTime.format(new Date(sale.sold_at))}</p>
    </header>
    <div class="receipt-table-wrap"><table class="receipt-lines">
      <colgroup><col class="receipt-col-product"><col class="receipt-col-quantity"><col class="receipt-col-price"><col class="receipt-col-amount"></colgroup>
      <thead><tr><th>Producto</th><th>Cant.</th><th>Precio</th><th>Importe</th></tr></thead>
      <tbody>${lines}</tbody>
    </table></div>
    <section class="receipt-summary">
      <div><span>Subtotal</span><strong>${money(subtotal)}</strong></div>
      ${adjustmentLine}
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
  renderRejectedPackages();
  renderSales();
  renderPurchases();
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

function renderDailySalesChart(sales, start, end) {
  const container = document.querySelector("#daily-sales-chart");
  const totals = new Map(sales.map((row) => [row.activity_date, row.net_cents]));
  const first = new Date(`${start}T00:00:00`);
  const last = new Date(`${end}T00:00:00`);
  const dayCount = Math.floor((last - first) / 86400000) + 1;
  const dates = [];
  if (Number.isFinite(dayCount) && dayCount > 0 && dayCount <= 3660) {
    for (let offset = 0; offset < dayCount; offset += 1) {
      const day = new Date(first);
      day.setDate(day.getDate() + offset);
      dates.push(`${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`);
    }
  } else {
    dates.push(...totals.keys());
  }
  if (!dates.length) {
    container.innerHTML = '<p class="empty-cell">No hay ventas en este período.</p>';
    return;
  }

  const values = dates.map((date) => totals.get(date) || 0);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const left = 78;
  const right = 780;
  const top = 18;
  const bottom = 192;
  const x = (index) => dates.length === 1 ? (left + right) / 2 : left + index * (right - left) / (dates.length - 1);
  const y = (value) => bottom - ((value - min) / span) * (bottom - top);
  const points = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const labels = dates.map((date, index) => {
    const show = dates.length <= 8 || index === 0 || index === dates.length - 1 || index % Math.ceil(dates.length / 6) === 0;
    return show ? `<text x="${x(index)}" y="218" text-anchor="middle">${escapeHtml(date.slice(5))}</text>` : "";
  }).join("");
  const ticks = [0, 1, 2, 3].map((step) => {
    const value = min + span * step / 3;
    const position = y(value);
    return `<line x1="${left}" y1="${position}" x2="${right}" y2="${position}" class="chart-gridline"/><text x="${left - 10}" y="${position + 4}" text-anchor="end">${escapeHtml(money(value))}</text>`;
  }).join("");
  const markers = values.map((value, index) =>
    `<circle cx="${x(index)}" cy="${y(value)}" r="3"><title>${escapeHtml(dates[index])}: ${escapeHtml(money(value))}</title></circle>`,
  ).join("");
  container.innerHTML = `<svg viewBox="0 0 800 238" preserveAspectRatio="none" aria-hidden="true">${ticks}<polyline points="${points}" class="chart-line"/>${markers}${labels}</svg>`;
}

function renderPaymentMethodChart(sales) {
  const container = document.querySelector("#payment-method-chart");
  const totals = new Map();
  sales.forEach((row) => totals.set(row.payment_method, (totals.get(row.payment_method) || 0) + row.net_cents));
  const rows = [...totals].map(([method, cents]) => ({ method, cents })).sort((a, b) => b.cents - a.cents);
  if (!rows.length) {
    container.innerHTML = '<p class="chart-empty">Sin ventas en este período.</p>';
    return;
  }
  const max = Math.max(1, ...rows.map((row) => Math.abs(row.cents)));
  const center = 410;
  const scale = 275 / max;
  const height = rows.length * 42 + 16;
  const bars = rows.map((row, index) => {
    const y = index * 42 + 7;
    const width = Math.max(1, Math.abs(row.cents) * scale);
    const x = row.cents < 0 ? center - width : center;
    const valueX = row.cents < 0 ? x - 9 : x + width + 9;
    const anchor = row.cents < 0 ? "end" : "start";
    const amount = money(row.cents);
    return `<text x="8" y="${y + 17}">${escapeHtml(row.method)}</text><rect x="${x}" y="${y}" width="${width}" height="25" rx="4" class="${row.cents < 0 ? "chart-bar-negative" : "chart-bar"}"><title>${escapeHtml(row.method)}: ${escapeHtml(amount)}</title></rect><text x="${valueX}" y="${y + 17}" text-anchor="${anchor}">${escapeHtml(amount)}</text>`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 800 ${height}" role="img" aria-label="Ventas netas por medio de pago">${bars}<line x1="${center}" y1="0" x2="${center}" y2="${height}" class="chart-baseline"/></svg>`;
}

function renderTopProductsChart(products) {
  const container = document.querySelector("#top-products-chart");
  const rows = products.slice(0, 10);
  if (!rows.length) {
    container.innerHTML = '<p class="chart-empty">Sin ventas en este período.</p>';
    return;
  }
  const max = Math.max(1, ...rows.map((row) => row.net_units));
  const height = rows.length * 46 + 10;
  const bars = rows.map((row, index) => {
    const y = index * 46 + 6;
    const width = Math.max(1, row.net_units / max * 260);
    const name = row.name.length > 28 ? `${row.name.slice(0, 27)}…` : row.name;
    return `<text x="8" y="${y + 17}"><title>${escapeHtml(row.name)}</title>${escapeHtml(name)}</text><rect x="230" y="${y}" width="${width}" height="26" rx="4" class="chart-bar"><title>${row.net_units} unidades</title></rect><text x="505" y="${y + 17}">${row.net_units} u.</text><text x="585" y="${y + 17}">Margen ${escapeHtml(money(row.margin_cents))}</text>`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 800 ${height}" role="img" aria-label="Diez productos más vendidos y margen">${bars}</svg>`;
}

function renderCategorySalesChart(categories) {
  const container = document.querySelector("#category-sales-chart");
  if (!categories.length) {
    container.innerHTML = '<p class="chart-empty">Sin productos vendidos en este período.</p>';
    return;
  }
  const max = Math.max(1, ...categories.map((row) => row.net_units));
  const barLeft = 210;
  const maxBarWidth = 340;
  const unitsX = 570;
  const revenueX = 655;
  const rowHeight = 52;
  const top = 32;
  const height = categories.length * rowHeight + top + 8;
  const bars = categories.map((row, index) => {
    const y = top + index * rowHeight;
    const width = Math.max(2, row.net_units / max * maxBarWidth);
    const name = row.category.length > 25 ? `${row.category.slice(0, 24)}…` : row.category;
    const revenue = money(row.net_revenue_cents);
    const details = `${row.net_units} unidades · ventas netas ${revenue}`;
    return `<text x="8" y="${y + 20}"><title>${escapeHtml(row.category)}</title>${escapeHtml(name)}</text><rect x="${barLeft}" y="${y}" width="${width}" height="30" rx="5" class="chart-bar"><title>${escapeHtml(details)}</title></rect><text x="${unitsX}" y="${y + 20}"><title>Unidades vendidas netas</title>${row.net_units} u.</text><text x="${revenueX}" y="${y + 20}"><title>Ventas netas</title>${escapeHtml(revenue)}</text>`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 800 ${height}" role="img" aria-label="Productos vendidos y venta neta por categoría"><text x="${unitsX}" y="18" class="chart-heading">UNIDADES</text><text x="${revenueX}" y="18" class="chart-heading">VENTA NETA</text>${bars}</svg>`;
}

function renderInventoryMovementsChart(movements) {
  const container = document.querySelector("#inventory-movements-chart");
  const totals = new Map();
  movements.forEach((row) => {
    const day = totals.get(row.movement_date) || { incoming: 0, outgoing: 0 };
    if (row.quantity_delta > 0) day.incoming += row.quantity_delta;
    else day.outgoing += Math.abs(row.quantity_delta);
    totals.set(row.movement_date, day);
  });
  const dailyRows = [...totals].sort(([a], [b]) => a.localeCompare(b));
  if (!dailyRows.length) {
    container.innerHTML = '<p class="chart-empty">Sin movimientos de inventario en este período.</p>';
    return;
  }
  const aggregateByMonth = dailyRows.length > 45;
  const chartTotals = aggregateByMonth ? new Map() : totals;
  if (aggregateByMonth) {
    dailyRows.forEach(([date, day]) => {
      const month = date.slice(0, 7);
      const total = chartTotals.get(month) || { incoming: 0, outgoing: 0 };
      total.incoming += day.incoming;
      total.outgoing += day.outgoing;
      chartTotals.set(month, total);
    });
  }
  const rows = [...chartTotals].sort(([a], [b]) => a.localeCompare(b));
  const max = Math.max(1, ...rows.map(([, day]) => Math.max(day.incoming, day.outgoing)));
  const left = 62;
  const width = 720;
  const baseline = 112;
  const upHeight = 82;
  const downHeight = 82;
  const slot = width / rows.length;
  const barWidth = Math.max(3, Math.min(24, slot * 0.58));
  const bars = rows.map(([date, day], index) => {
    const x = left + (index + 0.5) * slot - barWidth / 2;
    const incomingHeight = day.incoming / max * upHeight;
    const outgoingHeight = day.outgoing / max * downHeight;
    const showLabel = rows.length <= 10 || index === 0 || index === rows.length - 1 || index % Math.ceil(rows.length / 8) === 0;
    const incoming = `<rect x="${x}" y="${baseline - incomingHeight}" width="${barWidth}" height="${incomingHeight}" class="chart-bar"><title>${escapeHtml(date)}: entrada ${day.incoming}</title></rect>`;
    const outgoing = `<rect x="${x}" y="${baseline}" width="${barWidth}" height="${outgoingHeight}" class="chart-bar-negative"><title>${escapeHtml(date)}: salida ${day.outgoing}</title></rect>`;
    const dateLabel = aggregateByMonth ? date.slice(2) : date.slice(5);
    const label = showLabel ? `<text x="${x + barWidth / 2}" y="218" text-anchor="middle">${escapeHtml(dateLabel)}</text>` : "";
    return `${incoming}${outgoing}${label}`;
  }).join("");
  const grid = [0, 1, 2].map((step) => {
    const offset = step * upHeight / 2;
    const value = Math.round(max * step / 2);
    return `<line x1="${left}" y1="${baseline - offset}" x2="${left + width}" y2="${baseline - offset}" class="chart-gridline"/><text x="${left - 8}" y="${baseline - offset + 3}" text-anchor="end">${value}</text><line x1="${left}" y1="${baseline + offset}" x2="${left + width}" y2="${baseline + offset}" class="chart-gridline"/><text x="${left - 8}" y="${baseline + offset + 3}" text-anchor="end">${step === 0 ? "" : value}</text>`;
  }).join("");
  const periodLabel = aggregateByMonth ? "por mes" : "por día";
  container.innerHTML = `<div class="report-chart-key"><span><i></i>Entradas</span><span><i class="key-negative"></i>Salidas</span><span>Total ${periodLabel}</span></div><svg viewBox="0 0 800 238" role="img" aria-label="Entradas sobre cero y salidas bajo cero ${periodLabel}">${grid}${bars}<line x1="${left}" y1="${baseline}" x2="${left + width}" y2="${baseline}" class="chart-baseline"/></svg>`;
}

function renderPackageStatusChart(packageCounts) {
  const container = document.querySelector("#package-status-chart");
  const destinations = new Map();
  packageCounts.forEach((row) => {
    const counts = destinations.get(row.destination) || { verified: 0, rejected: 0 };
    if (row.status === "Verificado") counts.verified = row.count;
    if (row.status === "Rechazado") counts.rejected = row.count;
    destinations.set(row.destination, counts);
  });
  const allRows = [...destinations].map(([destination, counts]) => ({ destination, ...counts }))
    .sort((a, b) => (b.verified + b.rejected) - (a.verified + a.rejected));
  if (!allRows.length) {
    container.innerHTML = '<p class="chart-empty">Sin bultos recibidos en este período.</p>';
    return;
  }
  const rows = allRows.slice(0, 5);
  if (allRows.length > rows.length) {
    const others = allRows.slice(rows.length).reduce(
      (total, row) => ({ destination: null, verified: total.verified + row.verified, rejected: total.rejected + row.rejected }),
      { destination: null, verified: 0, rejected: 0 },
    );
    rows.push(others);
  }
  const max = Math.max(1, ...rows.map((row) => row.verified + row.rejected));
  const scale = 430 / max;
  const height = rows.length * 44 + 42;
  const bars = rows.map((row, index) => {
    const y = index * 44 + 34;
    const verifiedWidth = row.verified * scale;
    const rejectedWidth = row.rejected * scale;
    const destination = row.destination ? locationName(row.destination) : "Otros destinos";
    return `<text x="8" y="${y + 17}">${escapeHtml(destination)}</text><rect x="205" y="${y}" width="${verifiedWidth}" height="26" rx="4" class="chart-bar-verified"><title>Verificados: ${row.verified}</title></rect><rect x="${205 + verifiedWidth}" y="${y}" width="${rejectedWidth}" height="26" rx="4" class="chart-bar-rejected"><title>Rechazados: ${row.rejected}</title></rect><text x="${213 + verifiedWidth + rejectedWidth}" y="${y + 17}">${row.verified} / ${row.rejected}</text>`;
  }).join("");
  container.innerHTML = `<div class="report-chart-key"><span><i></i>Verificados</span><span><i class="key-negative"></i>Rechazados</span><span>Verificados / rechazados</span></div><svg viewBox="0 0 800 ${height}" role="img" aria-label="Bultos verificados y rechazados por local destino">${bars}</svg>`;
}

async function loadReports(start, end) {
  const form = document.querySelector("#report-form");
  const values = start && end ? { start, end } : formObject(form);
  if (!values.start || !values.end) return;
  try {
    const query = new URLSearchParams({ start: values.start, end: values.end, location: values.location || "" });
    const report = await api(`/api/reports?${query}`);
    renderDailySalesChart(report.daily_sales, values.start, values.end);
    renderPaymentMethodChart(report.sales);
    document.querySelector("#report-sales").innerHTML = report.sales.map((row) => `<tr><td>${escapeHtml(row.activity_date)}</td><td>${escapeHtml(locationName(row.location))}</td><td>${escapeHtml(row.payment_method)}</td><td>${money(row.net_cents)}</td></tr>`).join("") || emptyRow(4, "Sin ventas en este período.");
    renderTopProductsChart(report.best_sellers);
    const topProducts = report.best_sellers.slice(0, 10);
    document.querySelector("#report-products").innerHTML = topProducts.map((row) => `<tr><td>${escapeHtml(row.name)}</td><td>${row.net_units}</td><td>${money(row.net_revenue_cents)}</td><td>${money(row.margin_cents)}</td></tr>`).join("") || emptyRow(4, "Sin ventas en este período.");
    renderCategorySalesChart(report.category_sales);
    const logistics = report.logistics;
    renderPackageStatusChart(logistics.package_counts);
    const average = logistics.average_transit_hours == null ? "—" : `${Number(logistics.average_transit_hours).toFixed(1)} h`;
    const wasteDetails = logistics.waste_movements.map((item) => `<p><b>${escapeHtml(item.waste_date)}</b> · ${escapeHtml(locationName(item.location))} · ${escapeHtml(productName(item.sku))} · ${item.waste_quantity} u. · ${money(item.valuation_cents)}${item.cost_is_estimated ? " (estimado)" : ""}</p>`).join("") || "<small>Sin mermas registradas en este período.</small>";
    document.querySelector("#report-logistics").innerHTML = `<div class="logistic-stat"><strong>${logistics.rejected_count}</strong><small>Bultos rechazados</small></div><div class="logistic-stat"><strong>${logistics.verified_packages}</strong><small>Bultos verificados</small></div><div class="logistic-stat"><strong>${average}</strong><small>Tránsito promedio del período</small></div><div class="logistic-stat"><strong>${logistics.waste_quantity} u.</strong><small>Mermas · ${money(logistics.waste_valuation_cents)}</small></div><div class="rejection-list"><strong>Bultos rechazados</strong>${logistics.rejected_packages.map((item) => `<p><b>${escapeHtml(item.code)}</b> · ${escapeHtml(locationName(item.destination))} · ${escapeHtml(item.reason || "Rechazado")}</p>`).join("") || "<small>Sin rechazos registrados en este período.</small>"}<strong>Mermas registradas</strong>${wasteDetails}<small>Los bultos rechazados requieren una resolución en el apartado Mermas.</small></div>`;
    renderInventoryMovementsChart(report.movements);
    document.querySelector("#report-movements").innerHTML = report.movements.map((row) => `<tr><td>${escapeHtml(row.movement_date)}</td><td>${escapeHtml(locationName(row.location))}</td><td>${escapeHtml(productName(row.sku))}</td><td>${escapeHtml(row.movement_type)}${row.cost_is_estimated ? "<br><small>Valorización estimada</small>" : ""}</td><td>${row.quantity_delta > 0 ? "+" : ""}${row.quantity_delta}</td><td>${money(row.valuation_delta_cents)}</td></tr>`).join("") || emptyRow(6, "Sin movimientos en este período.");
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

async function handleRejectedPackageDisposition(button) {
  const { packageCode, rejectedDisposition: disposition } = button.dataset;
  const action = disposition === "stock" ? "devolver al stock del CD" : "descartar y registrar como merma";
  if (!window.confirm(`¿Confirmás ${action} del bulto ${packageCode}?`)) return;
  button.disabled = true;
  try {
    const result = await api(`/api/packages/${encodeURIComponent(packageCode)}/disposition`, {
      method: "POST",
      body: JSON.stringify({ disposition }),
    });
    await refreshData();
    toast(result.result);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
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
      const sku = ask("Nuevo SKU (dejar vacío para conservarlo):", "");
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
      await send(`/api/products/${id}/update`, {
        sku: sku.trim() || item.sku, name, category, cost, price, minimum_stock,
        supplier_id: supplier_id.trim() ? Number(supplier_id) : null,
      });
    } else if (action === "product-delete") {
      const item = state.data.products.find((row) => row.id === id);
      if (!item || !window.confirm(`¿Eliminar ${item.name}? Los productos con stock u operaciones históricas están protegidos.`)) return;
      await send(`/api/products/${id}/delete`);
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
    option.textContent = `${line.name} · quedan ${line.quantity - line.returned_quantity}`;
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
  document.querySelector("#demo-access-buttons").addEventListener("click", (event) => {
    const button = event.target.closest("[data-demo-username]");
    if (!button) return;
    const account = state.demoShortcuts.find((item) => item.username === button.dataset.demoUsername);
    if (!account) return;
    const form = document.querySelector("#login-form");
    form.querySelector('[name="username"]').value = account.username;
    form.querySelector('[name="password"]').value = account.password;
    form.requestSubmit();
  });
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
      if (values.role !== "ADMIN") {
        body.create_location = { code: values.location_code, name: values.location_name };
      }
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
  document.querySelector("#package-destination").addEventListener("change", (event) => {
    selectPackageDestination(event.currentTarget.value);
  });
  document.querySelector("#package-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const body = formObject(event.currentTarget);
      body.items = readItemRows("package-items").reduce((result, item) => ({
        ...result,
        [item.sku]: (result[item.sku] || 0) + item.quantity,
      }), {});
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
      })).filter((payment) => Number(payment.amount) > 0);
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
    if (button.dataset.categoryAction === "rename") {
      state.editingCategoryId = category.id;
      renderCatalog();
      document.querySelector(`#category-edit-${category.id} [name="name"]`).focus();
      return;
    }
    if (button.dataset.categoryAction === "cancel") {
      state.editingCategoryId = null;
      renderCatalog();
      return;
    }
    try {
      if (!window.confirm(`¿Eliminar la categoría "${category.name}"?`)) return;
      await api(`/api/categories/${category.id}/delete`, { method: "POST", body: "{}" });
      await refreshData();
      toast("Categoría eliminada.");
    } catch (error) { toast(error.message, true); }
  });
  document.querySelector("#categories-table").addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-category-edit-form]");
    if (!form) return;
    event.preventDefault();
    const categoryId = Number(form.dataset.categoryEditForm);
    const name = form.elements.name.value.trim();
    if (!name) {
      toast("Ingresá un nombre para la categoría.", true);
      return;
    }
    try {
      await api(`/api/categories/${categoryId}/rename`, { method: "POST", body: JSON.stringify({ name }) });
      state.editingCategoryId = null;
      await refreshData();
      toast("Categoría actualizada.");
    } catch (error) { toast(error.message, true); }
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-master-action]");
    if (button) handleMasterAction(button);
  });
  document.querySelector("#stock-filter").addEventListener("change", renderInventory);
  document.querySelector("#package-filter").addEventListener("change", renderPackages);
  document.querySelector('#sale-form [name="location_code"]').addEventListener("change", updateSaleTotal);
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
    const form = event.currentTarget;
    const values = formObject(form);
    const body = {
      username: values.username,
      password: values.password,
      role: values.role,
      location_id: values.role === "LOCAL" ? Number(values.location_id) : null,
    };
    try {
      await api("/api/users", { method: "POST", body: JSON.stringify(body) });
      form.reset();
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
      for (const [key, value] of Object.entries(values)) {
        if (key.startsWith("quantity:")) {
          if (value !== "") quantities[key.slice("quantity:".length)] = Number(value);
        }
      }
      if (!Object.keys(quantities).length) {
        throw new Error("Ingresa el conteo físico de al menos un producto para cerrar.");
      }
      await api(`/api/inventory-counts/${form.dataset.countId}/lines`, { method: "POST", body: JSON.stringify({ quantities }) });
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
  document.querySelector("#add-minimum-stock-items").addEventListener("click", addMinimumStockSuggestions);
  document.querySelector("[data-add-payment]").addEventListener("click", () => {
    addPaymentRow();
    updateSaleTotal();
  });
  document.querySelector("#packages-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-package-action]");
    if (button) handlePackageAction(button);
  });
  document.querySelector("#rejected-packages-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-rejected-disposition]");
    if (button) handleRejectedPackageDisposition(button);
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
  updateSaleTotal();
  try {
    await initializeAuth();
  } catch (error) {
    toast(`No se pudo conectar con la aplicación: ${error.message}`, true);
  }
}

document.addEventListener("DOMContentLoaded", start);