# Diagrama de clases de la implementación actual

Este diagrama describe las clases y tablas de persistencia que existen hoy en
`sistema/`; no es el diagrama objetivo de diseño. Se divide en dos vistas para
facilitar su lectura.

## Capas y dependencias

```mermaid
classDiagram
    direction LR

    class Sistema
    class BaseDeDatos
    class FrontendHandler

    class ServicioCatalogo
    class ServicioInventario
    class ServicioReposicion
    class ServicioLogistica
    class ServicioVentas
    class ServicioReportes
    class AcreditadorExistencias
    class AuditorLogistico

    class Repositorio {
        +connection
    }
    class RepositorioEstablecimiento
    class RepositorioCatalogo
    class RepositorioExistencia
    class RepositorioReposicion
    class RepositorioBulto
    class RepositorioVenta
    class RepositorioProveedor
    class RepositorioMovimientoInventario

    class Establecimiento {
        +id
        +codigo
        +nombre
        +tipo
    }
    class Producto {
        +id
        +sku
        +nombre
        +categoria
        +costo_centavos
        +precio_centavos
        +proveedor_ids
    }
    class Existencia {
        <<table>>
        +cantidad
        +nivel_minimo
    }
    class EventoBulto {
        +bulto_id
        +estado
        +fecha
        +detalle
    }
    class VentaRegistrada {
        +id
        +comprobante
        +total_centavos
    }
    class DevolucionRegistrada {
        +id
        +importe_centavos
        +vuelve_al_stock
    }
    class LineaPedido {
        +sku
        +cantidad
    }

    Sistema *-- BaseDeDatos
    Sistema *-- ServicioCatalogo
    Sistema *-- ServicioInventario
    Sistema *-- ServicioReposicion
    Sistema *-- ServicioLogistica
    Sistema *-- ServicioVentas
    Sistema *-- ServicioReportes
    Establecimiento "1" -- "0..*" Existencia
    Producto "1" -- "0..*" Existencia

    ServicioCatalogo --> BaseDeDatos
    ServicioInventario --> BaseDeDatos
    ServicioReposicion --> BaseDeDatos
    ServicioLogistica --> BaseDeDatos
    ServicioVentas --> BaseDeDatos
    ServicioReportes --> BaseDeDatos
    AcreditadorExistencias --> BaseDeDatos
    AuditorLogistico --> BaseDeDatos

    FrontendHandler ..> Sistema : crea para cada operación

    Repositorio <|-- RepositorioEstablecimiento
    Repositorio <|-- RepositorioCatalogo
    Repositorio <|-- RepositorioExistencia
    Repositorio <|-- RepositorioReposicion
    Repositorio <|-- RepositorioBulto
    Repositorio <|-- RepositorioVenta
    Repositorio <|-- RepositorioProveedor
    Repositorio <|-- RepositorioMovimientoInventario

    ServicioCatalogo ..> RepositorioEstablecimiento
    ServicioCatalogo ..> RepositorioCatalogo
    ServicioInventario ..> RepositorioEstablecimiento
    ServicioInventario ..> RepositorioCatalogo
    ServicioInventario ..> RepositorioExistencia
    ServicioInventario ..> RepositorioBulto
    ServicioReposicion ..> RepositorioEstablecimiento
    ServicioReposicion ..> RepositorioCatalogo
    ServicioReposicion ..> RepositorioReposicion
    ServicioLogistica ..> RepositorioReposicion
    ServicioLogistica ..> RepositorioBulto
    ServicioLogistica ..> RepositorioCatalogo
    ServicioLogistica ..> RepositorioExistencia
    ServicioVentas ..> RepositorioEstablecimiento
    ServicioVentas ..> RepositorioCatalogo
    ServicioVentas ..> RepositorioExistencia
    ServicioVentas ..> RepositorioVenta
    AcreditadorExistencias ..> RepositorioBulto
    AcreditadorExistencias ..> RepositorioExistencia
    ServicioReportes ..> RepositorioEstablecimiento

    ServicioCatalogo ..> Establecimiento : devuelve
    ServicioLogistica ..> EventoBulto : publica
    ServicioVentas ..> VentaRegistrada : devuelve
    ServicioVentas ..> DevolucionRegistrada : devuelve
```

El nivel mínimo se configura por producto y establecimiento, no como una
propiedad global del producto. La entidad de persistencia `Existencia` relaciona
ambos conceptos y conserva la cantidad y el mínimo local.

Los servicios aplican las reglas de negocio y usan la conexión de `BaseDeDatos`;
las operaciones de escritura abren transacciones con ella. Los repositorios encapsulan consultas y escrituras SQLite; los
reportes y algunas actualizaciones también ejecutan SQL directamente desde el
servicio. `RepositorioProveedor` y `RepositorioMovimientoInventario` están
definidos, aunque las operaciones actuales acceden a esos datos desde otros
repositorios o consultas directas.

## Patrones y colaboraciones

```mermaid
classDiagram
    direction LR

    class FabricaEstablecimientos {
        +crear(id, codigo, nombre, tipo)
    }
    class Establecimiento
    class CentroDistribucion
    class Sucursal

    class EstrategiaCobro {
        <<Protocol>>
        +procesar(importe_centavos)
    }
    class PagoEfectivo
    class PagoTarjeta
    class PagoTransferencia
    class PagoCuentaCorriente
    class RegistroEstrategiasPago {
        +obtener(medio)
    }
    class ServicioVentas

    class EstadoBultoState {
        <<Protocol>>
        +siguiente(intacto, coincide_remito)
    }
    class MaquinaEstadosBulto {
        +siguiente(estado, intacto, coincide_remito)
    }
    class _EstadoPreparado
    class _EstadoPrecintado
    class _EstadoEnTransito
    class _EstadoRecibido
    class _EstadoFinal

    class ObservadorLogistico {
        <<Protocol>>
        +actualizar(evento)
    }
    class PublicadorEventos {
        <<Protocol>>
        +publicar(evento)
    }
    class BusEventos {
        +suscribir(observador)
        +publicar(evento)
    }
    class ReaccionLogistica
    class AcreditadorExistencias
    class AuditorLogistico
    class ServicioLogistica

    Establecimiento <|-- CentroDistribucion
    Establecimiento <|-- Sucursal
    FabricaEstablecimientos ..> CentroDistribucion : crea
    FabricaEstablecimientos ..> Sucursal : crea

    EstrategiaCobro <|.. PagoEfectivo
    EstrategiaCobro <|.. PagoTarjeta
    EstrategiaCobro <|.. PagoTransferencia
    EstrategiaCobro <|.. PagoCuentaCorriente
    RegistroEstrategiasPago o-- EstrategiaCobro : registra
    ServicioVentas --> RegistroEstrategiasPago

    EstadoBultoState <|.. _EstadoPreparado
    EstadoBultoState <|.. _EstadoPrecintado
    EstadoBultoState <|.. _EstadoEnTransito
    EstadoBultoState <|.. _EstadoRecibido
    EstadoBultoState <|.. _EstadoFinal
    MaquinaEstadosBulto ..> EstadoBultoState : selecciona estado

    ObservadorLogistico <|.. AcreditadorExistencias
    ObservadorLogistico <|.. AuditorLogistico
    PublicadorEventos <|.. ReaccionLogistica
    ReaccionLogistica *-- BusEventos
    BusEventos o-- ObservadorLogistico : notifica
    ServicioLogistica --> PublicadorEventos : publica eventos
```

La aplicación instancia la fábrica al crear establecimientos, selecciona una
estrategia de cobro por medio de pago y avanza el estado del bulto mediante
`MaquinaEstadosBulto`. En logística, `ReaccionLogistica` distribuye cada evento
a los observadores de inventario y auditoría. Al verificar la recepción, la
sucursal informa si el contenido coincide con el remito y si el precinto está
intacto; una discrepancia o un precinto violado rechaza el bulto.
