# Sistema de ventas, inventario y logística

Aplicación de consola para un comercio con Centro de Distribución y múltiples locales. Persiste los datos en SQLite en `data/retail.db`; no requiere dependencias externas.

## Ejecución

Desde la raíz del proyecto, inicia la interfaz web:

```powershell
python web_server.py
```

Abre `http://127.0.0.1:8000`. La pantalla de bienvenida permite iniciar sesión o registrar un usuario, eligiendo el perfil Local o Centro de Distribución. Los perfiles Local deben vincularse a una sucursal existente o nueva; los perfiles Centro se asocian al Centro de Distribución y no requieren una sucursal. En el primer inicio web, el botón **Registrar usuario** abre el asistente para crear una sola cuenta con el rol elegido; no hay credenciales predeterminadas. Las contraseñas deben tener al menos 10 caracteres. Desde **Perfiles**, el perfil Centro también puede crear cuentas adicionales, cambiar contraseñas y desactivar cuentas. Las sesiones web caducan a las 8 horas. El servidor y la aplicación web usan la misma base SQLite `data/retail.db`. Para detenerlo, presiona `Ctrl+C` en la terminal.

El perfil **Centro de Distribución** administra catálogo, ubicaciones, proveedores, compras, precios y cuentas; prepara, precinta y despacha bultos, y consulta los datos de la red. El perfil **Local** opera solo en la sucursal asociada: registra ventas y devoluciones, consulta inventario y reportes locales, solicita reposición, recibe y verifica sus bultos, y concilia conteos físicos. Los permisos se validan en la API además de ocultar las funciones no disponibles en la interfaz. `main.py` sigue siendo una interfaz de consola local y no usa estas sesiones web.

La interfaz de consola también sigue disponible:

```powershell
python main.py
```

Para comprobar el flujo completo con datos de ejemplo, sin alterar la base de datos local:

```powershell
python demo.py
```

El demo termina con `Demo completa: todo OK` si las validaciones del recorrido pasan.

Después de configurar los accesos, cargar productos y registrar el stock inicial del CD. Los importes se ingresan en unidades monetarias con dos decimales. Para ventas, devoluciones y órdenes se aceptan varios artículos separados por coma, con formato `SKU:cantidad`; los cobros usan `medio:importe`.

Las operaciones también están disponibles como API desde `RetailService`, instanciado mediante `create_application()` en `container.py`. El repositorio acepta `:memory:` para pruebas.

## Flujo logístico y reglas

1. Consolidar en una orden las necesidades de uno o más locales, con origen en el CD. La orden crea una suborden por destino para que cada local mantenga su propio remito, bultos y recepción.
2. Preparar uno o más bultos; se comprueba la cantidad solicitada y el stock disponible, incluyendo reservas de otros bultos.
3. Asignar un precinto y registrar el despacho. El stock del CD se descuenta al despachar.
4. Registrar la recepción y confirmar remito y precinto. Una no conformidad marca el bulto como `Rechazado`; sus productos no ingresan al stock del local.
5. Verificar el contenido para pasar a `Verificado`. Solo entonces se acredita stock vendible. Los eventos se guardan en una tabla con restricciones que impiden actualizarlos o borrarlos.

Las ventas validan el stock disponible y exigen que los cobros coincidan con el total. El stock local no se puede aumentar mediante ajustes manuales: se libera al verificar un bulto recibido o se corrige mediante un inventario físico conciliado. El comprobante web y su PDF muestran sucursal, número y fecha de venta, artículos, descuentos, subtotal, total y medios de pago, en un formato sencillo de ticket comercial; los lotes y números de serie se excluyen del comprobante. Las devoluciones deben referenciar una venta existente y respetar cantidades aún no devueltas; la inspección define si las unidades vuelven al stock o se registran como merma, y el reembolso se descuenta de los ingresos por medio de pago.

## Patrones de diseño

- **Repository:** `SQLiteRepository` centraliza la persistencia y las transacciones, aislando a los servicios de la conexión SQLite.
- **Strategy:** `PaymentStrategy` y sus implementaciones validan cada medio de pago; el registro de estrategias permite extender medios sin cambiar el flujo de venta.
- **State:** los estados de venta (`Confirmada`, `Devolución parcial`, `Devuelta`) determinan las transiciones al registrar devoluciones y bloquean operaciones no permitidas. Se persisten para reflejar el estado en la aplicación y en los comprobantes.

## Compras, precios, clientes y control de stock

- En **Compras**, el sistema sugiere cantidades por proveedor según faltantes de los locales, descontando stock disponible en el CD y compras pendientes. Las órdenes confirmadas se reciben total o parcialmente en el CD.
- En **Clientes y precios**, las ventas pueden asociarse a un cliente y una lista. Los precios de una lista se cargan como `SKU:precio`, separados por coma (por ejemplo, `SKU-001:1250.50,SKU-002:900`; usa punto para decimales). También se pueden crear promociones por SKU o categoría, con vigencia y descuento porcentual o fijo. Se aplica el mejor precio elegible; no se acumulan descuentos. La cuenta corriente respeta el límite del cliente y las ventas suman un punto por cada $10 de compra neta; las devoluciones revierten saldo y puntos correspondientes.
- En **Catálogo**, las categorías se crean, renombran y eliminan cuando no están asociadas a productos ni promociones; cada producto queda relacionado con una categoría administrada. Cada producto puede quedar sin lote/vencimiento, por lote o con seguimiento adicional por serie. Al ingresar stock en el CD o recibir una compra, el sistema genera un número aleatorio único para cada unidad; las series acompañan a las unidades en traslados, ventas, devoluciones y conteos. Los ingresos por lote aceptan vencimiento; las ventas consumen primero el lote vigente que vence antes (FEFO) y bloquean unidades vencidas. Las series disponibles se consultan en **Inventario** y no se muestran en el comprobante de venta.
- En **Catálogo**, productos, proveedores y locales se pueden editar; se pueden eliminar cuando no tienen existencias ni referencias históricas. El CD central se conserva como origen logístico. Los productos con actividad conservan SKU para no alterar la trazabilidad; las listas de precios se pueden editar o eliminar si no fueron aplicadas a ventas.
- En **Inventario**, una sesión de inventario físico permite contar los artículos, detallar lotes/series y conciliar diferencias al cerrarla. Los cambios generan movimientos auditables.
- Las tablas nuevas se crean al iniciar. Las columnas añadidas a productos, ventas, órdenes internas, líneas de venta y movimientos de inventario se migran automáticamente en bases SQLite existentes; las categorías previas se convierten al catálogo administrable.

## Reportes

`ReportService` permite filtrar los reportes por período y local. Incluye ventas netas de devoluciones por fecha/local/medio de pago, productos más vendidos y margen estimado, existencias y stock mínimo, bultos rechazados, mermas efectivas de devoluciones no aptas (con cantidad y costo), y tránsito promedio de los bultos verificados en el período y local elegidos. Los rechazos logísticos se informan aparte porque sus unidades vuelven al CD y no constituyen merma. Cada movimiento guarda el costo unitario vigente para su valorización histórica; los movimientos anteriores a esta migración se señalan como estimados porque se valorizan con el costo actual disponible al migrar.

## Pruebas

Instala las dependencias de desarrollo en el entorno virtual activo:

```powershell
python -m pip install -r requirements-dev.txt
```

Ejecuta las pruebas con pytest:

```powershell
python -m pytest -q
```

También puedes ejecutar las pruebas con la biblioteca estándar:

```powershell
python -m unittest discover -v
```
