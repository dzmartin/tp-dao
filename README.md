# Sistema de ventas, inventario y logística

Aplicación de consola para un comercio con Centro de Distribución y múltiples locales. Persiste los datos en SQLite en `data/retail.db`; no requiere dependencias externas.

## Ejecución

Desde la raíz del proyecto, inicia la interfaz web:

```powershell
python web_server.py
```

Abre `http://127.0.0.1:8000`. El servidor y la aplicación web usan la misma base SQLite `data/retail.db`. Para detenerlo, presiona `Ctrl+C` en la terminal.

La interfaz de consola también sigue disponible:

```powershell
python main.py
```

Para comprobar el flujo completo con datos de ejemplo, sin alterar la base de datos local:

```powershell
python demo.py
```

El demo termina con `Demo completa: todo OK` si las validaciones del recorrido pasan.

Crear primero una ubicación tipo `CD` y al menos un `LOCAL`, luego cargar productos y registrar el stock inicial del CD. Los importes se ingresan en unidades monetarias con dos decimales. Para ventas, devoluciones y órdenes se aceptan varios artículos separados por coma, con formato `SKU:cantidad`; los cobros usan `medio:importe`.

Las operaciones también están disponibles como API desde `RetailService`, instanciado mediante `create_application()` en `container.py`. El repositorio acepta `:memory:` para pruebas.

## Flujo logístico y reglas

1. Crear una orden con origen en el CD y destino en un local.
2. Preparar uno o más bultos; se comprueba la cantidad solicitada y el stock disponible, incluyendo reservas de otros bultos.
3. Asignar un precinto y registrar el despacho. El stock del CD se descuenta al despachar.
4. Registrar la recepción y confirmar remito y precinto. Una no conformidad marca el bulto como `Rechazado`; sus productos no ingresan al stock del local.
5. Verificar el contenido para pasar a `Verificado`. Solo entonces se acredita stock vendible. Los eventos se guardan en una tabla con restricciones que impiden actualizarlos o borrarlos.

Las ventas validan el stock disponible y exigen que los cobros coincidan con el total. Los comprobantes identifican cada venta. Las devoluciones deben referenciar una venta existente y respetar cantidades aún no devueltas; la inspección define si las unidades vuelven al stock o se registran como merma, y el reembolso se descuenta de los ingresos por medio de pago.

## Patrones de diseño

- **Repository:** `SQLiteRepository` centraliza la persistencia y las transacciones, aislando a los servicios de la conexión SQLite.
- **Strategy:** `PaymentStrategy` y sus implementaciones validan cada medio de pago; el registro de estrategias permite extender medios sin cambiar el flujo de venta.

## Reportes

`ReportService` implementa reportes por período/local/medio de pago netos de devoluciones, productos más vendidos y margen estimado, existencias/stock mínimo/valorización, rechazos logísticos y tiempo promedio de tránsito, además del detalle de movimientos y su valorización.

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
