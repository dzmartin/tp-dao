# Sistema de ventas, inventario y logística

Aplicación para un comercio con Centro de Distribución y múltiples locales. Persiste los datos en SQLite en `backend/data/retail.db`; no requiere dependencias externas.

## Estructura

La aplicación conserva Python estándar, SQLite y la interfaz HTML/JavaScript. La estructura principal sigue la del ZIP de referencia, sin migrar el stack:

- `backend/api`: servidor HTTP y API.
- `backend/dominio`: entidades, excepciones y reglas de dominio.
- `backend/persistencia`: repositorio SQLite.
- `backend/servicios`: autenticación, operaciones comerciales y reportes.
- `backend/datos`: semilla y cuentas de demostración.
- `backend/main.py`, `backend/demo.py`: entradas de consola y demostración.
- `backend/data`: base de datos principal y base de demostración.
- `frontend`: página web y recursos JavaScript/CSS en `frontend/src`.
- `tests`: pruebas automatizadas.
- `docs`: documentación, material del proyecto y [diagrama entidad-relación](docs/DER.md).
- `requirements.txt`: dependencias; `tests/requirements-dev.txt`: dependencias de desarrollo.

## Ejecución

Desde la raíz del proyecto, inicia la interfaz web:

```powershell
python -m backend.api.web_server
```

Abre `http://127.0.0.1:8000`. En el primer inicio, **Registrar usuario** permite crear la cuenta inicial: Administrador, Centro de Distribución o Local. El Administrador no se vincula a una ubicación; al crearlo se agrega el CD central si todavía no existe. Los perfiles Local deben vincularse a una sucursal existente o nueva; Centro no requiere una sucursal. No hay credenciales predeterminadas y las contraseñas deben tener al menos 10 caracteres. El Administrador crea usuarios, administra productos, categorías, proveedores y ubicaciones. Centro opera inventario y envíos; Local registra ventas y recepciones de su sucursal. Los permisos se validan en la API además de ocultar las funciones no disponibles en la interfaz. Las sesiones web caducan a las 8 horas. El servidor y la aplicación web usan la misma base SQLite `backend/data/retail.db`. Para detenerlo, presiona `Ctrl+C` en la terminal.

Para crear datos aleatorios de demostración en una base separada (sin modificar `backend/data/retail.db`):

```powershell
python -m backend.datos.seed
```

La semilla genera 15 productos con precios y categorías asignados aleatoriamente, ubicaciones, stock, envíos con horarios distintos para los hitos de preparado, precintado, en tránsito, recibido y verificado, y dos bultos rechazados (uno pendiente de resolución y otro descartado como merma). Los movimientos logísticos llevan fecha actual para aparecer en el reporte predeterminado del mes. También genera ventas distribuidas en días y horarios aleatorios de los últimos 30 días y cuatro cuentas de prueba en `backend/data/retail-demo.db`. Para abrir esa base, inicia el servidor con `python -m backend.api.web_server --database backend/data/retail-demo.db`; en el inicio de sesión aparecerán botones para entrar como Administrador, Centro de Distribución o Local. Las cuentas son `demo-admin` / `EstrellaAdmin26`, `demo-cd` / `EstrellaCD26`, `demo-local-centro` / `EstrellaLocal1` y `demo-local-norte` / `EstrellaLocal2`. Estas credenciales son solo para la base demo; no las uses en producción. Puedes cambiar el período con `--days 14` y reproducir los mismos datos en una base vacía con `--seed 42`. Los reportes muestran un gráfico de ventas netas diarias, descontando devoluciones.

El perfil **Administrador** gestiona usuarios, catálogo, ubicaciones, proveedores e inventario. **Centro de Distribución** opera el stock central, prepara y despacha envíos, y consulta reportes y mermas. **Local** registra ventas y devoluciones, consulta el inventario de su sucursal, solicita reposición, recibe y verifica envíos, y consulta reportes locales. Los permisos se validan en la API además de ocultar las funciones no disponibles en la interfaz. `backend/main.py` sigue siendo una interfaz de consola local y no usa estas sesiones web.

La interfaz de consola también sigue disponible:

```powershell
python -m backend.main
```

Para comprobar el flujo completo con datos de ejemplo, sin alterar la base de datos local:

```powershell
python -m backend.demo
```

El demo termina con `Demo completa: todo OK` si las validaciones del recorrido pasan.

Después de configurar los accesos, cargar productos y registrar el stock inicial del CD. Los importes se ingresan en unidades monetarias con dos decimales. Para ventas, devoluciones y órdenes se aceptan varios artículos separados por coma, con formato `SKU:cantidad`; los cobros usan `medio:importe`.

Las operaciones también están disponibles como API desde `RetailService`, instanciado mediante `create_application()` en `backend/container.py`. El repositorio acepta `:memory:` para pruebas.

## Flujo logístico y reglas

1. Consolidar en una orden las necesidades de uno o más locales, con origen en el CD. La orden crea una suborden por destino; cada local puede modificar los productos y cantidades de su suborden mientras siga `Abierta`.
2. Preparar bultos por local. El resumen reúne las cantidades pendientes de todas sus órdenes abiertas; cada bulto puede cubrir varias órdenes del mismo destino, asignando primero a la más antigua y conservando la trazabilidad por orden. Se comprueban las cantidades solicitadas y el stock disponible, incluyendo reservas de otros bultos.
3. Asignar un precinto al bulto.
4. Antes del despacho, el CD puede cancelar un bulto `Preparado` o `Precintado`. Se recalculan las cantidades pendientes de las órdenes y se conserva el historial del bulto; no se permite cancelar después del despacho.
5. Registrar el despacho. El stock del CD se descuenta al despachar.
6. Registrar la recepción y confirmar remito y precinto. Una no conformidad marca el bulto como `Rechazado`; sus productos no ingresan al stock del local.
7. Verificar el contenido para pasar a `Verificado`. Solo entonces se acredita stock vendible. Los eventos se guardan en una tabla con restricciones que impiden actualizarlos o borrarlos.

Las ventas validan el stock disponible y ajustan el precio según el medio de pago: tarjeta suma 10 %, transferencia mantiene el precio y efectivo descuenta 20 %. Si se mezclan medios, cada parte de la venta recibe el ajuste de su propio medio; el efectivo puede superar el importe pendiente de su parte y generar vuelto. El stock local no se puede aumentar mediante ajustes manuales: se libera al verificar un bulto recibido o se corrige mediante un inventario físico conciliado. El comprobante web y su PDF muestran sucursal, número y fecha de venta, artículos, subtotal, ajuste, total y medios de pago, en un formato sencillo de ticket comercial. Las devoluciones deben referenciar una venta existente y respetar cantidades aún no devueltas; la inspección define si las unidades vuelven al stock o se registran como merma, y el reembolso se descuenta de los ingresos por medio de pago.

## Patrones de diseño

- **Repository:** `SQLiteRepository` centraliza la persistencia y las transacciones, aislando a los servicios de la conexión SQLite.
- **Strategy:** `PaymentStrategy` y sus implementaciones validan los cobros y calculan el importe ajustado para efectivo, tarjeta y transferencia dentro del flujo de venta.
- **State:** los estados de venta (`Confirmada`, `Devolución parcial`, `Devuelta`) determinan las transiciones al registrar devoluciones y bloquean operaciones no permitidas. Se persisten para reflejar el estado en la aplicación y en los comprobantes.

## Compras y control de stock

- En **Compras**, el sistema sugiere cantidades por proveedor según faltantes de los locales, descontando stock disponible en el CD y compras pendientes. Las órdenes confirmadas se reciben total o parcialmente en el CD.
- En **Ventas**, se admiten cobros divididos entre efectivo, tarjeta y transferencia; al dividirlos, cada parte recibe el descuento o recargo correspondiente y los informes, comprobantes y devoluciones reflejan los importes efectivamente cobrados.
- En **Catálogo**, las categorías se crean, renombran y eliminan cuando no están asociadas a productos; cada producto queda relacionado con una categoría administrada.
- En **Catálogo**, productos, proveedores y locales se pueden editar; se pueden eliminar cuando no tienen existencias ni referencias históricas. El CD central se conserva como origen logístico. Los productos con actividad conservan SKU para no alterar su historial.
- En **Inventario**, una sesión de inventario físico permite contar los artículos y conciliar diferencias al cerrarla. Los cambios generan movimientos auditables.
- Las tablas nuevas se crean al iniciar. Las columnas añadidas a productos, ventas, órdenes internas, líneas de venta y movimientos de inventario se migran automáticamente en bases SQLite existentes; las categorías previas se convierten al catálogo administrable. Al actualizar, se eliminan los datos de clientes, listas, promociones y metadatos de descuentos; se conservan los totales históricos de ventas, pagos, devoluciones y movimientos.

## Reportes

`ReportService` permite filtrar los reportes por período y local. Incluye el gráfico diario de ventas netas, ventas netas de devoluciones por fecha/local/medio de pago, productos más vendidos y margen estimado, existencias y stock mínimo, bultos rechazados, mermas efectivas de devoluciones no aptas (con cantidad y costo), y tránsito promedio de los bultos verificados en el período y local elegidos. Los rechazos logísticos se informan aparte porque sus unidades vuelven al CD y no constituyen merma. Cada movimiento guarda el costo unitario vigente para su valorización histórica; los movimientos anteriores a esta migración se señalan como estimados porque se valorizan con el costo actual disponible al migrar.

Desde la pantalla Reportes se puede descargar un PDF resumen con el período y local seleccionados, los indicadores de ventas, productos, categorías, inventario, logística y movimientos disponibles para el perfil. El documento identifica a quien lo solicitó y la fecha y hora de generación; también incluye domicilio y CUIT de los locales del alcance. Los usuarios Local solo pueden generar el informe de su propia sucursal.

## Pruebas

Instala las dependencias de desarrollo en el entorno virtual activo:

```powershell
python -m pip install -r tests/requirements-dev.txt
```

Ejecuta las pruebas con pytest:

```powershell
python -m pytest -q
```

También puedes ejecutar las pruebas con la biblioteca estándar:

```powershell
python -m unittest discover -v
```
