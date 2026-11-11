# Diagrama de clases del diseño objetivo

Este documento presenta el diseño objetivo del sistema de gestión de una red de
centros de distribución y sucursales. Describe las responsabilidades y
relaciones deseadas, no la estructura exacta de las clases que existen hoy.
Para comparar con el código implementado, consultar
[DIAGRAMA_DE_CLASES.md](./DIAGRAMA_DE_CLASES.md).

## Dominio

```mermaid
classDiagram
    direction LR

    class Establecimiento {
        <<abstract>>
        +id
        +codigo
        +nombre
    }
    class CentroDistribucion
    class Sucursal
    class Producto {
        +id
        +sku
        +nombre
        +costo_centavos
        +precio_centavos
    }
    class Categoria {
        +id
        +nombre
    }
    class Proveedor {
        +id
        +identificador_fiscal
        +nombre
    }
    class Existencia {
        +cantidad
        +nivel_minimo
    }
    class MovimientoInventario {
        +tipo
        +cantidad
        +costo_unitario_centavos
        +referencia
        +fecha
    }
    class LoteReposicion {
        +id
        +fecha_creacion
    }
    class PedidoReposicion {
        +id
        +estado
        +fecha_creacion
    }
    class LineaPedido {
        +cantidad_solicitada
        +cantidad_preparada
    }
    class Bulto {
        +id
        +precinto
        +estado
        +fecha_preparacion
    }
    class LineaBulto {
        +cantidad_embalada
    }
    class Remito {
        +numero
        +fecha_emision
    }
    class LineaRemito {
        +cantidad_declarada
    }
    class RecepcionBulto {
        +fecha
        +precinto_intacto
        +coincide_con_remito
        +observaciones
        +determinar_estado() EstadoBulto
    }
    class LineaRecepcion {
        +cantidad_contada
    }
    class EventoBulto {
        +estado
        +fecha
        +detalle
    }
    class Venta {
        +id
        +comprobante
        +estado
        +fecha
        +total_centavos
    }
    class LineaVenta {
        +cantidad
        +precio_unitario_centavos
        +costo_unitario_centavos
    }
    class Pago {
        +medio
        +importe_centavos
        +tipo
        +fecha
    }
    class Devolucion {
        +id
        +fecha
        +importe_centavos
    }
    class LineaDevolucion {
        +cantidad
        +reingresa_al_stock
    }

    Establecimiento <|-- CentroDistribucion
    Establecimiento <|-- Sucursal

    Categoria "1" <-- "0..*" Producto : clasifica
    Producto "0..*" -- "0..*" Proveedor : abastecido por
    Establecimiento "1" *-- "0..*" Existencia : mantiene
    Producto "1" -- "0..*" Existencia : cuantifica
    Establecimiento "1" *-- "0..*" MovimientoInventario : registra
    Producto "1" -- "0..*" MovimientoInventario : afecta

    LoteReposicion "1" *-- "1..*" PedidoReposicion : consolida
    CentroDistribucion "1" <-- "0..*" PedidoReposicion : origen
    Sucursal "1" <-- "0..*" PedidoReposicion : destino
    PedidoReposicion "1" *-- "1..*" LineaPedido : solicita
    Producto "1" <-- "0..*" LineaPedido : solicitado
    PedidoReposicion "1" *-- "0..*" Bulto : se prepara en
    Bulto "1" *-- "1..*" LineaBulto : contiene
    Producto "1" <-- "0..*" LineaBulto : embalado
    Bulto "1" *-- "1" Remito : acompana
    Remito "1" *-- "1..*" LineaRemito : declara
    Producto "1" <-- "0..*" LineaRemito : declarado
    Bulto "1" *-- "0..1" RecepcionBulto : se inspecciona al recibir
    RecepcionBulto "1" *-- "0..*" LineaRecepcion : registra conteo
    Producto "1" <-- "0..*" LineaRecepcion : contado
    Bulto "1" *-- "1..*" EventoBulto : historial

    Sucursal "1" <-- "0..*" Venta : realiza
    Venta "1" *-- "1..*" LineaVenta : incluye
    Producto "1" <-- "0..*" LineaVenta : vendido
    Venta "1" *-- "1..*" Pago : cobra
    Venta "1" *-- "0..*" Devolucion : puede originar
    Devolucion "1" *-- "1..*" LineaDevolucion : detalla
    LineaVenta "1" <-- "0..*" LineaDevolucion : devuelve
```

El stock se consulta por la combinación de establecimiento y producto. Cada
cambio de existencia genera un movimiento valorizado y auditable; el nivel
mínimo también pertenece a esa combinación, por lo que puede variar entre
establecimientos para el mismo producto. Los pedidos de un lote comparten un
centro de origen, pero cada pedido tiene una sucursal destino; se preparan en
uno o más bultos. El bulto representa el contenedor
físico y sus líneas indican lo embalado; el remito es el documento que declara
los productos y cantidades que deberían estar dentro. Al recibirlo, la sucursal
compara el contenido con el remito y registra el estado del precinto. Si el
contenido coincide y el precinto está intacto, el bulto pasa a `Verificado`; si
el contenido no coincide o el precinto está violado, pasa a `Rechazado`. Solo
los bultos verificados habilitan el ingreso de sus unidades al stock vendible.
Las devoluciones pueden ser parciales y especifican por línea si el producto
vuelve al inventario.

## Aplicación, servicios y persistencia

```mermaid
classDiagram
    direction LR

    class InterfazWeb
    class InterfazCLI
    class Sistema {
        +catalogo
        +inventario
        +reposicion
        +logistica
        +ventas
        +reportes
    }
    class ServicioCatalogo
    class ServicioInventario
    class ServicioReposicion
    class ServicioLogistica
    class ServicioVentas
    class ServicioReportes
    class UnidadDeTrabajo {
        <<interface>>
        +confirmar()
        +revertir()
    }
    class BaseDeDatosSQLite {
        +conectar()
    }
    class UnidadDeTrabajoSQLite
    class RepositorioEstablecimiento {
        <<interface>>
    }
    class RepositorioCatalogo {
        <<interface>>
    }
    class RepositorioExistencia {
        <<interface>>
    }
    class RepositorioReposicion {
        <<interface>>
    }
    class RepositorioBulto {
        <<interface>>
    }
    class RepositorioVenta {
        <<interface>>
    }
    class RepositorioReportes {
        <<interface>>
    }
    class RepositorioEstablecimientoSQLite
    class RepositorioCatalogoSQLite
    class RepositorioExistenciaSQLite
    class RepositorioReposicionSQLite
    class RepositorioBultoSQLite
    class RepositorioVentaSQLite
    class RepositorioReportesSQLite

    InterfazWeb ..> Sistema : solicita operaciones
    InterfazCLI ..> Sistema : solicita operaciones
    Sistema *-- ServicioCatalogo
    Sistema *-- ServicioInventario
    Sistema *-- ServicioReposicion
    Sistema *-- ServicioLogistica
    Sistema *-- ServicioVentas
    Sistema *-- ServicioReportes

    ServicioCatalogo --> RepositorioEstablecimiento
    ServicioCatalogo --> RepositorioCatalogo
    ServicioInventario --> RepositorioEstablecimiento
    ServicioInventario --> RepositorioCatalogo
    ServicioInventario --> RepositorioExistencia
    ServicioReposicion --> RepositorioEstablecimiento
    ServicioReposicion --> RepositorioCatalogo
    ServicioReposicion --> RepositorioReposicion
    ServicioLogistica --> RepositorioReposicion
    ServicioLogistica --> RepositorioBulto
    ServicioVentas --> RepositorioEstablecimiento
    ServicioVentas --> RepositorioCatalogo
    ServicioVentas --> RepositorioExistencia
    ServicioVentas --> RepositorioVenta
    ServicioReportes --> RepositorioReportes

    ServicioCatalogo ..> UnidadDeTrabajo : transacción
    ServicioInventario ..> UnidadDeTrabajo : transacción
    ServicioReposicion ..> UnidadDeTrabajo : transacción
    ServicioLogistica ..> UnidadDeTrabajo : transacción
    ServicioVentas ..> UnidadDeTrabajo : transacción
    UnidadDeTrabajoSQLite ..|> UnidadDeTrabajo
    RepositorioEstablecimientoSQLite ..|> RepositorioEstablecimiento
    RepositorioCatalogoSQLite ..|> RepositorioCatalogo
    RepositorioExistenciaSQLite ..|> RepositorioExistencia
    RepositorioReposicionSQLite ..|> RepositorioReposicion
    RepositorioBultoSQLite ..|> RepositorioBulto
    RepositorioVentaSQLite ..|> RepositorioVenta
    RepositorioReportesSQLite ..|> RepositorioReportes
    UnidadDeTrabajoSQLite --> BaseDeDatosSQLite : administra conexión
    RepositorioEstablecimientoSQLite --> BaseDeDatosSQLite : usa conexión
    RepositorioCatalogoSQLite --> BaseDeDatosSQLite : usa conexión
    RepositorioExistenciaSQLite --> BaseDeDatosSQLite : usa conexión
    RepositorioReposicionSQLite --> BaseDeDatosSQLite : usa conexión
    RepositorioBultoSQLite --> BaseDeDatosSQLite : usa conexión
    RepositorioVentaSQLite --> BaseDeDatosSQLite : usa conexión
    RepositorioReportesSQLite --> BaseDeDatosSQLite : usa conexión
```

Las interfaces de usuario dependen de la fachada `Sistema`, no de la base de
datos. Los servicios coordinan reglas de negocio y transacciones; los contratos de
repositorio aíslan la persistencia. Los adaptadores SQLite implementan esos
contratos y usan `BaseDeDatosSQLite` para acceder a la conexión. Los reportes
consultan su propio contrato de lectura, evitando acoplar la lógica de
aplicación a consultas SQL.

## Patrones de diseño

```mermaid
classDiagram
    direction LR

    class FabricaEstablecimientos {
        +crear(tipo, datos)
    }
    class Establecimiento
    class CentroDistribucion
    class Sucursal

    class EstrategiaCobro {
        <<interface>>
        +procesar(importe_centavos)
    }
    class PagoEfectivo
    class PagoTarjeta
    class PagoTransferencia
    class PagoCuentaCorriente
    class ServicioVentas

    class EstadoBulto {
        <<interface>>
        +siguiente()
    }
    class EstadoPreparado
    class EstadoPrecintado
    class EstadoEnTransito
    class EstadoRecibido
    class EstadoVerificado
    class EstadoRechazado
    class Bulto

    class PublicadorEventos {
        <<interface>>
        +publicar(evento)
    }
    class ObservadorLogistico {
        <<interface>>
        +actualizar(evento)
    }
    class BusEventos
    class AcreditadorExistencias
    class AuditorLogistico
    class ServicioLogistica

    Establecimiento <|-- CentroDistribucion
    Establecimiento <|-- Sucursal
    FabricaEstablecimientos ..> Establecimiento : crea según tipo

    EstrategiaCobro <|.. PagoEfectivo
    EstrategiaCobro <|.. PagoTarjeta
    EstrategiaCobro <|.. PagoTransferencia
    EstrategiaCobro <|.. PagoCuentaCorriente
    ServicioVentas o--> EstrategiaCobro : selecciona por medio

    EstadoBulto <|.. EstadoPreparado
    EstadoBulto <|.. EstadoPrecintado
    EstadoBulto <|.. EstadoEnTransito
    EstadoBulto <|.. EstadoRecibido
    EstadoBulto <|.. EstadoVerificado
    EstadoBulto <|.. EstadoRechazado
    Bulto o--> EstadoBulto : delega transición
    EstadoRecibido --> EstadoVerificado : remito coincide y precinto intacto
    EstadoRecibido --> EstadoRechazado : remito no coincide o precinto violado

    PublicadorEventos <|.. BusEventos
    ObservadorLogistico <|.. AcreditadorExistencias
    ObservadorLogistico <|.. AuditorLogistico
    BusEventos o--> ObservadorLogistico : notifica
    ServicioLogistica --> PublicadorEventos : publica transición
```

La fábrica encapsula la creación según el tipo de establecimiento. La estrategia
de cobro permite validar cada medio de pago de forma independiente. El estado
del bulto gobierna sus transiciones válidas, incluida la verificación del
precinto. La publicación de eventos logísticos mantiene desacoplados el
movimiento de existencias y la auditoría.

## Criterios del diseño

- Mantener las reglas de negocio en servicios y el estado del dominio en
  entidades y objetos de valor.
- Hacer cumplir invariantes: ventas solo en sucursales, ajustes manuales solo en
  el centro, stock no negativo, pedidos atendidos según cantidades pendientes
  y bultos acreditados solo después de su verificación.
- Persistir movimientos, pagos y eventos para conservar trazabilidad.
- Mantener opcionales fuera de este alcance las compras sugeridas, promociones,
  listas de precios especiales, lotes de productos e inventario físico.
