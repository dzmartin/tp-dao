SALESPERSON_NAMES = (
    "Lucía Fernández",
    "Mateo González",
    "Sofía Rodríguez",
    "Benjamín López",
    "Valentina Martínez",
    "Joaquín Pérez",
    "Mila Romero",
    "Tomás Herrera",
    "Camila Torres",
    "Santiago Díaz",
    "Martina Castro",
    "Facundo Álvarez",
    "Julieta Ruiz",
    "Nicolás Acosta",
    "Agustina Molina",
    "Franco Medina",
    "Emilia Suárez",
    "Lautaro Giménez",
    "Catalina Sosa",
    "Thiago Benítez",
    "Florencia Navarro",
    "Ramiro Pereyra",
    "Abril Domínguez",
    "Bruno Cabrera",
)


def next_salesperson_name(used_names: set[str]) -> str:
    return next(
        (name for name in SALESPERSON_NAMES if name not in used_names),
        SALESPERSON_NAMES[len(used_names) % len(SALESPERSON_NAMES)],
    )
