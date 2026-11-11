# Implementación independiente del Tema 3

Esta carpeta contiene una implementación nueva basada en la consigna y en el
[diagrama de clases de diseño](../tpdao/DIAGRAMA_DE_CLASES_DISENO.md). No importa módulos de la aplicación existente.
Requiere Python 3.11 o superior y usa solamente la biblioteca estándar
(SQLite incluido).

## Alcance implementado

- Administración de centros de distribución, sucursales, proveedores,
  productos, categorías y edición de precios. El stock mínimo se configura por
  producto en cada establecimiento desde la tabla de inventario.
- Existencias y movimientos auditables. Los ajustes manuales se limitan al
  centro de distribución.
- Pedidos de reposición iniciados por cada sucursal. El centro atiende pedidos
  pendientes y puede consolidar los de varias sucursales en un lote común, con
  una orden de origen/destino y un bulto por sucursal.
- Preparación y ciclo de vida del bulto: preparado, precintado, en tránsito,
  recibido, verificado o rechazado. En la recepción se comprueba que el contenido
  coincida con el remito y que el precinto esté intacto para acreditar existencias
  vendibles en el destino.
- Ventas con uno o varios productos y medios de pago, comprobante, validación
  de stock, consulta del comprobante en PDF y devoluciones parciales o completas
  con reintegro y opción de reingreso al inventario.
- Reportes de ventas/ingresos, productos/margen estimado, existencias/mínimos,
  bultos rechazados y tránsito promedio, además de movimientos valorizados.
- Patrones Factory, State, Strategy, Observer y Repository.

Las funcionalidades opcionales de la consigna (compras sugeridas, promociones,
listas de precios especiales, lotes e inventario físico) quedan para una
iteración posterior.

## Diagrama de clases

El diseño objetivo del dominio, las capas y los patrones está en
[DIAGRAMA_DE_CLASES_OBJETIVO.md](./DIAGRAMA_DE_CLASES_OBJETIVO.md). El diagrama
de la implementación actual, con sus capas y patrones, está en
[DIAGRAMA_DE_CLASES.md](./DIAGRAMA_DE_CLASES.md).

## Diagrama entidad-relación

El DER basado en las tablas SQLite actuales está en [DER.md](./DER.md).

## Ejecutar

Desde esta carpeta:

```powershell
py -m sistema init --database data\sistema.db
py -m sistema demo --database data\demo.db
py -m sistema reports --database data\demo.db --from-date 2026-01-01 --to-date 2026-12-31
py -m sistema web --database data\sistema.db
```

La demostración crea datos con identificadores únicos y recorre el flujo de
reposiciones, bulto verificado y venta. Los servicios también se pueden usar
directamente desde Python:

```python
from sistema import Sistema

with Sistema("data/sistema.db") as sistema:
    print(sistema.reportes.existencias_y_minimos())
```

El comando `web` inicia una interfaz local en `http://127.0.0.1:8000` para
dar de alta establecimientos y productos, registrar ventas, ajustar existencias
del centro, preparar reposiciones, avanzar el seguimiento logístico y consultar
reportes. Se puede cambiar la dirección con `--host` y `--port`; por defecto
solo acepta conexiones locales.

Al ingresar, elegí el perfil del centro o de una sucursal:

- **Centro de distribución:** administra sucursales, productos y proveedores;
  ajusta el stock del centro, consulta los pedidos de las sucursales y prepara
  y despacha reposiciones consolidadas, además de consultar inventario de la
  red y reportes. Al preparar cada envío, indica la cantidad de cada producto:
  no puede superar el stock disponible ni lo que queda pendiente, y el saldo
  sin preparar permanece en el pedido de la sucursal. Los envíos que todavía no
  salieron del centro se pueden cancelar para liberar el stock y devolver las
  cantidades al pedido pendiente.
- **Sucursal:** registra ventas y devoluciones, consulta únicamente su stock y
  solicita la reposición de productos al centro y recibe/verifica los bultos
  destinados a ella. Puede tener un pedido pendiente a la vez.

El primer uso permite crear el centro inicial. El selector de perfil no usa
contraseñas y sirve para separar el contexto operativo de la aplicación local;
no reemplaza la autenticación de usuarios para un despliegue compartido.

## Probar

Desde la carpeta del proyecto:

```powershell
py -m unittest discover -s tests -v
```

Los importes se expresan como enteros de centavos para evitar errores de
redondeo. La capa de servicios aplica las reglas de negocio y los repositorios
encapsulan las consultas SQLite.
