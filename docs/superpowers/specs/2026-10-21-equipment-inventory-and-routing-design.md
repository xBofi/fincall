# Equipment Inventory and Who Gets the Incident

## Why

Real call, 2026-10-21. A neighbour reports the door-entry panel destroyed by vandals. The bot:

1. classified it as `electricidad` and read the category aloud;
2. took "sí, bueno… un electricista no lo podrá resolver, sino alguien que instale fonoporteros"
   as a confirmation;
3. sent Electricidad Gómez.

A door-entry system is serviced by its installer or maintainer. Vandalism usually goes to the
community's insurance, and replacing the unit needs the administrator's approval. The bot should
have registered the incident for the administrator, with a suggestion.

## Business rules (Provisional, 2026-10-21)

1. **The bot notifies a professional directly only if all of these hold:**
   - the incident is a **repair** (not a replacement or works, not insurance or a third party,
     not a neighbour dispute, not inside a private home);
   - there is a professional for it: the **maintainer of the affected equipment**, if the
     estate has one on record, otherwise the specialist for the category;
   - the neighbour confirmed the summary **without objecting** to how it will be handled.
2. **Otherwise the incident is registered for the administrator.** It is never refused. It
   carries the reason and the suggested professional: the equipment's maintainer with brand,
   model and contract, if known.
3. **Equipment maintainer before generic specialist.** Each estate can list its equipment
   (door-entry, lift, garage door, pressure pump, aerial, fire protection…) with brand, model,
   location, installer, current maintainer and their support contact, maintenance contract and
   warranty end.
4. **The summary does not read the category or the urgency to the neighbour.** They are
   internal. The close says who will handle it.
5. **"Sí, pero…" with an objection confirms the facts but not the handling.** The objection is
   recorded and the incident goes to the administrator. A correction of facts (problem, home,
   estate) is still handled as a correction.

Pending: the administrator must provide each estate's equipment and maintenance contracts. Demo
data is used until then.

## Data

- **New entity `Equipo`:** `id`, `finca_id`, `tipo`, `ubicacion`, `marca`, `modelo`,
  `instalador`, `mantenedor_id` (an `Especialista` of the administrador, so it reuses its
  channel and contact), `contrato`, `garantia_hasta`.
- **`Incidencia`** gains `equipo_id`, `actuacion` and `sugerencia` (the text for the
  administrator).
- **Migration `0007`:** table `equipos` plus the three columns on `incidencias`.

## Understanding the call (no extra LLM call)

The turn interpretation already classifies in the same call. The classification block gains
three keys:

- `actuacion`: `reparacion` (default) | `reposicion` | `siniestro` | `convivencia` |
  `privativo`;
- `equipo`: the id of the affected equipment, chosen from that estate's short list in the
  prompt. Only when it clearly matches, otherwise omitted;
- `objecion`: what the neighbour disagrees with, when they accept but object.

The standalone classifier (`clasificar_problema`) gains `actuacion` and `equipo`. The estate's
equipment list is loaded into the call state once, when the estate is confirmed.

## Graph

- `asignar_especialista` decides, in this order:
  1. objection or `actuacion` other than `reparacion` → administrator (`motivo_revision`);
  2. the equipment's maintainer;
  3. the category's specialist;
  4. administrator.
  The suggestion is built in every case.
- `avisar_canal` gets a new close for administrator cases that are not merely "no specialist":
  "Se la paso al administrador, que la revisará hoy mismo y {contacto} para decirle cómo se va
  a resolver."
- `confirmar_resumen.resumen` becomes "Le resumo: {quien}{resumen}. ¿Es correcto?". The old
  variables are still passed, so a published override does not break. The `categoria` fragment
  templates are removed.

## Measured

New gold cases in `gold_interpretacion.jsonl` (with the estate's equipment) for `actuacion`,
`equipo` and `objecion`, plus graph tests for each routing branch. Vertex accuracy and latency
are compared before and after (3 runs).
