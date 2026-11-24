"""Builds the training set of the turn classifier (`nlp/data/train.jsonl`).

The gold sets in `evals/` are the TEST set and are never trained on. The training data is
hand-written here (Spanish first, then English and Catalan), in the style of what callers say,
and augmented with seeded openers and fillers. Each row carries every label the classifier
learns:

    intent     continue | repeat | correct | human | hang_up | out_of_scope | question
    category   catalog code (only for incidents)
    urgency    low | medium | high | emergency (only for incidents)
    work_type  repair | replacement | insurance_claim | neighbour_dispute | private_unit

A leakage check drops any generated row too similar to a gold utterance (normalised
SequenceMatcher ratio >= 0.85), so the gold scores stay honest.

Usage (deterministic: same seed, same file):
    uv run python nlp/data/build_dataset.py --seed 13
"""

from __future__ import annotations

import argparse
import json
import random
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALS = HERE.parents[1] / "evals"

# (text, category, urgency, work_type)
Incident = tuple[str, str, str, str]

ES: list[Incident] = [
    # plumbing
    ("Se ha roto una tubería en el garaje y sale mucha agua", "plumbing", "high", "repair"),
    ("Tenemos un atasco en el desagüe del patio y el agua no baja", "plumbing", "medium", "repair"),
    ("El grifo del cuarto de limpieza no cierra del todo", "plumbing", "low", "repair"),
    ("No hay agua en ningún piso del edificio desde esta mañana", "plumbing", "high", "repair"),
    ("Hay una gotera que viene de la bajante de la escalera", "plumbing", "medium", "repair"),
    ("El sumidero de la terraza comunitaria está atascado", "plumbing", "medium", "repair"),
    ("Sale agua sucia por la arqueta de la entrada", "plumbing", "high", "repair"),
    ("Se está inundando el cuarto de contadores del agua", "plumbing", "high", "repair"),
    ("Hay poca presión de agua en todo el bloque", "plumbing", "medium", "repair"),
    ("Pierde agua la llave de paso general del portal", "plumbing", "medium", "repair"),
    ("Huele a alcantarilla en el sótano, creo que es el desagüe", "plumbing", "medium", "repair"),
    ("La fuente del jardín comunitario pierde agua por la tubería", "plumbing", "low", "repair"),
    (
        "Habría que cambiar todas las bajantes, son de fibrocemento",
        "plumbing",
        "low",
        "replacement",
    ),
    # electrical
    ("No hay luz en el garaje y no se ve nada", "electrical", "medium", "repair"),
    ("El temporizador de la escalera apaga la luz enseguida", "electrical", "low", "repair"),
    ("Un enchufe del cuarto de bicicletas echa chispas", "electrical", "high", "repair"),
    ("Está fundido el fluorescente del trastero comunitario", "electrical", "low", "repair"),
    ("Se ha ido la luz de todas las zonas comunes", "electrical", "high", "repair"),
    ("La farola del patio parpadea toda la noche", "electrical", "low", "repair"),
    (
        "El cuadro eléctrico de la comunidad hace un zumbido fuerte",
        "electrical",
        "medium",
        "repair",
    ),
    ("El sensor de movimiento del rellano no enciende la luz", "electrical", "low", "repair"),
    (
        "El pulsador de la luz del tercero está suelto y se ven los cables",
        "electrical",
        "medium",
        "repair",
    ),
    ("Las luces de emergencia del garaje no se encienden", "electrical", "medium", "repair"),
    ("El timbre del portal no funciona", "electrical", "low", "repair"),
    ("Hay un cable pelado colgando en la escalera", "electrical", "high", "repair"),
    ("Queremos poner luces led en toda la escalera", "electrical", "low", "replacement"),
    # elevator
    ("El ascensor no baja de la quinta planta", "elevator", "high", "repair"),
    ("La puerta del ascensor se cierra de golpe y casi me pilla", "elevator", "medium", "repair"),
    ("El ascensor no para en el segundo piso", "elevator", "medium", "repair"),
    ("Se ha quedado mi vecino encerrado en el ascensor", "elevator", "emergency", "repair"),
    ("El ascensor está fuera de servicio desde ayer", "elevator", "high", "repair"),
    ("La cabina del ascensor no queda a nivel del suelo", "elevator", "medium", "repair"),
    ("El botón de llamada del ascensor en el bajo está roto", "elevator", "low", "repair"),
    ("El ascensor chirría mucho cuando sube", "elevator", "low", "repair"),
    ("No funciona la luz de dentro del ascensor", "elevator", "low", "repair"),
    ("El ascensor se para entre plantas a veces", "elevator", "high", "repair"),
    (
        "Hay una persona mayor atrapada en el ascensor y no responde",
        "elevator",
        "emergency",
        "repair",
    ),
    ("El ascensor tiene el espejo roto", "elevator", "low", "repair"),
    ("Habría que cambiar el ascensor entero, es muy antiguo", "elevator", "low", "replacement"),
    # cleaning
    ("La escalera está muy sucia, no la han limpiado esta semana", "cleaning", "low", "repair"),
    ("Hay cristales rotos en el suelo del portal", "cleaning", "medium", "repair"),
    ("Los contenedores del cuarto de basuras están desbordados", "cleaning", "medium", "repair"),
    ("Alguien ha dejado bolsas de basura en el rellano", "cleaning", "low", "repair"),
    ("El suelo del garaje está lleno de aceite y resbala", "cleaning", "medium", "repair"),
    ("Hay excrementos de perro en la entrada del edificio", "cleaning", "low", "repair"),
    ("Los cristales del portal están muy sucios", "cleaning", "low", "repair"),
    ("Han vomitado en el ascensor y huele muy mal", "cleaning", "medium", "repair"),
    ("Hay muchas hojas y barro en la rampa del garaje", "cleaning", "low", "repair"),
    ("El cuarto de basuras huele a podrido", "cleaning", "medium", "repair"),
    # locksmith
    ("La puerta del garaje no se abre con el mando", "locksmith", "medium", "repair"),
    ("La cerradura del portal está atascada y no gira la llave", "locksmith", "medium", "repair"),
    (
        "Se ha roto la llave dentro de la cerradura del cuarto de contadores",
        "locksmith",
        "medium",
        "repair",
    ),
    ("La puerta de la azotea no cierra y entra cualquiera", "locksmith", "medium", "repair"),
    ("El muelle de la puerta del portal está roto y da portazos", "locksmith", "low", "repair"),
    ("Necesito una copia de la llave del trastero comunitario", "locksmith", "low", "repair"),
    ("El buzón tiene la cerradura forzada", "locksmith", "low", "insurance_claim"),
    ("La puerta de emergencia del garaje no abre desde dentro", "locksmith", "high", "repair"),
    ("Han reventado la cerradura del portal esta noche", "locksmith", "high", "insurance_claim"),
    ("El portón del garaje se ha quedado a medio abrir", "locksmith", "medium", "repair"),
    (
        "No puedo abrir la puerta del cuarto de bicis, la llave no entra",
        "locksmith",
        "low",
        "repair",
    ),
    # masonry
    ("Se ha caído un trozo de techo en el rellano del cuarto", "masonry", "high", "repair"),
    ("Hay humedades en la pared del garaje", "masonry", "low", "repair"),
    (
        "Hay una grieta en la pared de la escalera que cada vez es más grande",
        "masonry",
        "medium",
        "repair",
    ),
    ("Se están levantando las baldosas del patio", "masonry", "low", "repair"),
    ("Se ha desprendido parte del balcón de la fachada", "masonry", "emergency", "repair"),
    ("Hay un escalón roto en la escalera del portal", "masonry", "medium", "repair"),
    ("El falso techo del portal tiene una mancha de agua", "masonry", "low", "repair"),
    ("Se cae el yeso de la pared del trastero", "masonry", "low", "repair"),
    ("Hay un agujero en el suelo de la rampa del garaje", "masonry", "medium", "repair"),
    ("Se ha agrietado el muro del jardín y parece que se va a caer", "masonry", "high", "repair"),
    ("Una tormenta ha arrancado tejas del tejado", "masonry", "high", "insurance_claim"),
    (
        "Un coche ha golpeado la pared del garaje y la ha agrietado",
        "masonry",
        "medium",
        "insurance_claim",
    ),
    ("Queremos rehacer toda la fachada", "masonry", "low", "replacement"),
    # painting
    ("Han hecho grafitis en la puerta del garaje", "painting", "low", "insurance_claim"),
    ("La pintura de la escalera está muy desconchada", "painting", "low", "repair"),
    ("Hay que pintar el portal, está muy estropeado", "painting", "low", "replacement"),
    ("Están saliendo desconchones en las paredes del rellano", "painting", "low", "repair"),
    ("Han pintado la pared del ascensor con spray", "painting", "low", "insurance_claim"),
    ("La barandilla de la escalera necesita pintura, está oxidada", "painting", "low", "repair"),
    ("Hay una pintada ofensiva en la entrada", "painting", "medium", "insurance_claim"),
    # gardening
    ("El césped del jardín está muy alto", "gardening", "low", "repair"),
    ("Un árbol del patio está inclinado y puede caerse", "gardening", "high", "repair"),
    ("Hay que podar los árboles que tocan las ventanas", "gardening", "low", "repair"),
    ("El riego automático del jardín no funciona", "gardening", "low", "repair"),
    ("Las plantas de la entrada se están secando", "gardening", "low", "repair"),
    ("Hay raíces levantando el suelo del jardín", "gardening", "low", "repair"),
    ("Las ramas del pino tapan la farola del patio", "gardening", "low", "repair"),
    ("Hay malas hierbas por toda la zona verde", "gardening", "low", "repair"),
    # general_maintenance
    ("La antena colectiva no da señal y no se ve la tele", "general_maintenance", "low", "repair"),
    ("Toca revisar los extintores de la escalera", "general_maintenance", "low", "repair"),
    ("La calefacción central no calienta", "general_maintenance", "high", "repair"),
    (
        "La caldera comunitaria hace ruidos y no da agua caliente",
        "general_maintenance",
        "high",
        "repair",
    ),
    (
        "La ventilación del garaje no funciona y huele a humo de coches",
        "general_maintenance",
        "medium",
        "repair",
    ),
    ("Los buzones están descolgados de la pared", "general_maintenance", "low", "repair"),
    ("La barrera del aparcamiento no sube", "general_maintenance", "medium", "repair"),
    # administrative
    ("Quería pedir un certificado de deuda de la comunidad", "administrative", "low", "repair"),
    ("No me ha llegado la convocatoria de la junta", "administrative", "low", "repair"),
    ("Necesito el acta de la junta extraordinaria", "administrative", "low", "repair"),
    ("Me han cobrado dos veces el recibo de la comunidad", "administrative", "low", "repair"),
    (
        "Quiero cambiar la cuenta bancaria donde me pasan el recibo",
        "administrative",
        "low",
        "repair",
    ),
    ("Querría ver el presupuesto anual de la comunidad", "administrative", "low", "repair"),
    (
        "Necesito los estatutos de la comunidad para vender el piso",
        "administrative",
        "low",
        "repair",
    ),
    # unclassifiable: no failing element, emergencies without a category, pests, nonsense
    ("Hay olor a quemado en el garaje y sale humo", "unclassifiable", "emergency", "repair"),
    ("Hay fuego en un contenedor del portal", "unclassifiable", "emergency", "repair"),
    (
        "Hay un escape de gas en la escalera, huele muchísimo",
        "unclassifiable",
        "emergency",
        "repair",
    ),
    ("Hay ratas en el cuarto de basuras", "unclassifiable", "medium", "repair"),
    ("Tenemos una plaga de chinches en el edificio", "unclassifiable", "medium", "repair"),
    ("Hay un nido de avispas en la terraza", "unclassifiable", "medium", "repair"),
    ("No va", "unclassifiable", "medium", "repair"),
    ("Se ha roto", "unclassifiable", "medium", "repair"),
    ("Hace un ruido muy raro", "unclassifiable", "low", "repair"),
    ("Lo de siempre, que no funciona", "unclassifiable", "medium", "repair"),
    ("Sigue igual que la semana pasada", "unclassifiable", "medium", "repair"),
    ("Mi gato se ha subido al tejado", "unclassifiable", "low", "repair"),
    ("Hay un señor desconocido durmiendo en el portal", "unclassifiable", "medium", "repair"),
    # neighbour disputes and private units
    (
        "El vecino de arriba pone la música altísima todas las noches",
        "unclassifiable",
        "low",
        "neighbour_dispute",
    ),
    (
        "Los del cuarto tienden la ropa y me gotea en mi terraza",
        "unclassifiable",
        "low",
        "neighbour_dispute",
    ),
    ("El perro del vecino ladra todo el día", "unclassifiable", "low", "neighbour_dispute"),
    ("Un vecino aparca en mi plaza de garaje", "unclassifiable", "low", "neighbour_dispute"),
    ("Se me ha roto el grifo de la cocina de mi casa", "plumbing", "low", "private_unit"),
    ("No me funciona un enchufe dentro de mi piso", "electrical", "low", "private_unit"),
    ("La cisterna de mi baño pierde agua", "plumbing", "low", "private_unit"),
    ("La persiana de mi dormitorio no sube", "general_maintenance", "low", "private_unit"),
    ("Al vecino de abajo le cae agua de mi baño", "plumbing", "medium", "private_unit"),
]

EN: list[Incident] = [
    ("A pipe burst in the garage and water is pouring out", "plumbing", "high", "repair"),
    ("The drain in the courtyard is blocked", "plumbing", "medium", "repair"),
    ("There's no water in the whole building", "plumbing", "high", "repair"),
    ("Water is leaking from the ceiling of the stairwell", "plumbing", "medium", "repair"),
    ("The tap in the communal laundry won't stop running", "plumbing", "low", "repair"),
    ("The hallway lights are out on the fourth floor", "electrical", "medium", "repair"),
    ("A socket in the bike room is sparking", "electrical", "high", "repair"),
    ("The stair light timer goes off too fast", "electrical", "low", "repair"),
    ("The emergency lights in the car park don't work", "electrical", "medium", "repair"),
    ("There's an exposed wire hanging in the corridor", "electrical", "high", "repair"),
    ("The lift is stuck on the sixth floor", "elevator", "high", "repair"),
    ("My neighbour is trapped inside the elevator", "elevator", "emergency", "repair"),
    ("The elevator doors close too fast", "elevator", "medium", "repair"),
    ("The lift makes a grinding noise", "elevator", "low", "repair"),
    ("The elevator call button on the ground floor is broken", "elevator", "low", "repair"),
    ("The bins in the trash room are overflowing", "cleaning", "medium", "repair"),
    ("There's broken glass all over the lobby floor", "cleaning", "medium", "repair"),
    ("The stairs haven't been cleaned in weeks", "cleaning", "low", "repair"),
    ("Someone left rubbish bags on the landing", "cleaning", "low", "repair"),
    ("The garage door won't open with the remote", "locksmith", "medium", "repair"),
    ("The front door lock is jammed", "locksmith", "medium", "repair"),
    ("The roof door doesn't lock and anyone can get in", "locksmith", "medium", "repair"),
    (
        "Someone broke the lock of the main entrance last night",
        "locksmith",
        "high",
        "insurance_claim",
    ),
    ("A piece of the ceiling fell in the hallway", "masonry", "high", "repair"),
    ("There's damp on the garage wall", "masonry", "low", "repair"),
    ("There's a big crack spreading across the stairwell wall", "masonry", "medium", "repair"),
    ("A balcony piece came off the facade", "masonry", "emergency", "repair"),
    ("The storm ripped tiles off the roof", "masonry", "high", "insurance_claim"),
    ("Someone sprayed graffiti on the garage door", "painting", "low", "insurance_claim"),
    ("The paint in the stairwell is peeling", "painting", "low", "repair"),
    ("The lawn in the garden needs mowing", "gardening", "low", "repair"),
    ("A tree in the courtyard is leaning and might fall", "gardening", "high", "repair"),
    ("The garden sprinklers aren't working", "gardening", "low", "repair"),
    ("The shared TV aerial has no signal", "general_maintenance", "low", "repair"),
    ("The central heating isn't working", "general_maintenance", "high", "repair"),
    ("I need a certificate saying I'm up to date with the fees", "administrative", "low", "repair"),
    ("I didn't get the notice for the owners' meeting", "administrative", "low", "repair"),
    ("I was charged the community fee twice", "administrative", "low", "repair"),
    ("There's smoke coming out of the basement", "unclassifiable", "emergency", "repair"),
    ("I can smell gas on the stairs", "unclassifiable", "emergency", "repair"),
    ("There are rats in the bin room", "unclassifiable", "medium", "repair"),
    ("It's broken again", "unclassifiable", "medium", "repair"),
    ("It doesn't work", "unclassifiable", "medium", "repair"),
    (
        "The neighbour upstairs plays loud music every night",
        "unclassifiable",
        "low",
        "neighbour_dispute",
    ),
    ("Someone keeps parking in my garage space", "unclassifiable", "low", "neighbour_dispute"),
    ("The kitchen tap in my flat is broken", "plumbing", "low", "private_unit"),
    ("A socket inside my apartment doesn't work", "electrical", "low", "private_unit"),
]

CA: list[Incident] = [
    ("S'ha trencat una canonada al garatge i surt molta aigua", "plumbing", "high", "repair"),
    ("No hi ha aigua a tot l'edifici", "plumbing", "high", "repair"),
    ("No funciona el llum de l'escala", "electrical", "medium", "repair"),
    ("Un endoll del traster fa espurnes", "electrical", "high", "repair"),
    ("L'ascensor s'ha aturat i hi ha una persona tancada", "elevator", "emergency", "repair"),
    ("L'ascensor fa un soroll estrany", "elevator", "low", "repair"),
    ("Els contenidors del quarto d'escombraries estan plens", "cleaning", "medium", "repair"),
    ("La porta del garatge no s'obre amb el comandament", "locksmith", "medium", "repair"),
    ("El pany del portal està encallat", "locksmith", "medium", "repair"),
    ("Hi ha una esquerda a la paret de l'escala", "masonry", "medium", "repair"),
    ("Ha caigut un tros de sostre al replà", "masonry", "high", "repair"),
    ("Han fet pintades a la façana", "painting", "low", "insurance_claim"),
    ("Cal podar els arbres del pati", "gardening", "low", "repair"),
    ("Necessito un certificat de la comunitat", "administrative", "low", "repair"),
    ("Fa olor de gas a l'escala", "unclassifiable", "emergency", "repair"),
    ("Hi ha rates al soterrani", "unclassifiable", "medium", "repair"),
]

# (text, intent)
INTENTS_ES: list[tuple[str, str]] = [
    ("¿Cómo dice?", "repeat"),
    ("No le he entendido, ¿me lo repite?", "repeat"),
    ("Perdone, se ha cortado, ¿qué me decía?", "repeat"),
    ("¿Puede repetirlo más despacio?", "repeat"),
    ("No le oigo bien, repita por favor", "repeat"),
    ("¿Qué? No me he enterado", "repeat"),
    ("Otra vez, por favor", "repeat"),
    ("¿Me lo puede volver a decir?", "repeat"),
    ("No, perdone, me he equivocado de piso", "correct"),
    ("Espere, la puerta es la C, no la B", "correct"),
    ("No es el segundo, es el primero", "correct"),
    ("Me he confundido, el portal es el uno", "correct"),
    ("Rectifico, la calle es Alcalá, no Atocha", "correct"),
    ("Perdón, quería decir el ático", "correct"),
    ("Eso no está bien, mi piso es el sexto", "correct"),
    ("No, no, el número es el catorce", "correct"),
    ("Páseme con alguien de la administración", "human"),
    ("Prefiero hablar con una persona de verdad", "human"),
    ("Quiero hablar con el administrador", "human"),
    ("¿Me puede atender un humano?", "human"),
    ("No quiero hablar con un robot, póngame con alguien", "human"),
    ("Quiero que me llame una persona", "human"),
    ("Póngame con un operador", "human"),
    ("Necesito hablar con alguien de la oficina", "human"),
    ("Vale, nada más, adiós", "hang_up"),
    ("Ya está, muchas gracias, hasta luego", "hang_up"),
    ("Déjelo, ya llamaré mañana", "hang_up"),
    ("No quiero seguir, cuelgo", "hang_up"),
    ("Bueno, pues nada, adiós", "hang_up"),
    ("Gracias, eso era todo", "hang_up"),
    ("Me tengo que ir, otro día llamo", "hang_up"),
    ("Hasta luego, gracias", "hang_up"),
    ("¿Cuánto me toca pagar de derrama?", "out_of_scope"),
    ("Quería alquilar mi plaza de garaje", "out_of_scope"),
    ("¿Me puede dar el teléfono del vecino del quinto?", "out_of_scope"),
    ("¿Saben de algún piso en venta en el edificio?", "out_of_scope"),
    ("Quiero votar en contra de la obra del tejado", "out_of_scope"),
    ("¿Cuándo pasan el recibo este mes?", "out_of_scope"),
    ("¿Me recomienda un fontanero para mi casa?", "out_of_scope"),
    ("Quiero presentarme a presidente de la comunidad", "out_of_scope"),
    ("¿Esto lo graban?", "question"),
    ("¿Cuánto tardarán en venir?", "question"),
    ("¿Quién me va a llamar?", "question"),
    ("¿Por qué me pide el piso?", "question"),
    ("¿Es usted una máquina?", "question"),
    ("¿Me avisarán cuando lo arreglen?", "question"),
    ("¿Lo paga la comunidad o lo pago yo?", "question"),
    ("¿Para qué quiere mis datos?", "question"),
]

INTENTS_EN: list[tuple[str, str]] = [
    ("Sorry, can you say that again?", "repeat"),
    ("I didn't catch that", "repeat"),
    ("What was that?", "repeat"),
    ("Could you repeat it more slowly?", "repeat"),
    ("No, sorry, I got the floor wrong", "correct"),
    ("Wait, it's door C, not B", "correct"),
    ("Actually it's the fifth floor, not the fourth", "correct"),
    ("I made a mistake, the street is Oak Avenue", "correct"),
    ("Let me talk to a real person", "human"),
    ("Can you put me through to the property manager?", "human"),
    ("I want to speak to someone from the office", "human"),
    ("I don't want a bot, get me a human", "human"),
    ("Okay, that's all, bye", "hang_up"),
    ("Never mind, I'll call back later", "hang_up"),
    ("Thanks, goodbye", "hang_up"),
    ("I have to go now, bye", "hang_up"),
    ("How much is my monthly fee?", "out_of_scope"),
    ("I want to rent out my parking space", "out_of_scope"),
    ("Can you give me my neighbour's phone number?", "out_of_scope"),
    ("Are there any flats for sale in the building?", "out_of_scope"),
    ("Is this call recorded?", "question"),
    ("How long will it take them to come?", "question"),
    ("Who is going to call me?", "question"),
    ("Are you a robot?", "question"),
]

INTENTS_CA: list[tuple[str, str]] = [
    ("Perdoni, m'ho pot repetir?", "repeat"),
    ("No l'he entès", "repeat"),
    ("No, perdoni, és el tercer pis, no el segon", "correct"),
    ("M'he equivocat de porta, és la A", "correct"),
    ("Vull parlar amb una persona", "human"),
    ("Passi'm amb l'administrador", "human"),
    ("Gràcies, adéu", "hang_up"),
    ("Ja trucaré demà, adéu", "hang_up"),
    ("Quant pago de comunitat cada mes?", "out_of_scope"),
    ("Vull llogar la meva plaça de pàrquing", "out_of_scope"),
    ("Aquesta trucada s'enregistra?", "question"),
    ("Quant trigaran a venir?", "question"),
]

# Non-incident "continue" answers: data the caller gives to the assistant's questions
CONTINUE_ES = [
    "Sí, eso es",
    "Correcto",
    "Calle Mayor número veinte",
    "Tercero B",
    "El bajo, puerta dos",
    "Me llamo Luis",
    "Soy Marta, del cuarto A",
    "Avenida de América cuarenta y dos",
    "Portal tres",
    "Sí, de acuerdo",
    "No, gracias, nada más que eso",
    "En la plaza España número cinco",
]
CONTINUE_EN = [
    "Yes, that's right",
    "Flat 4B",
    "It's 12 Elm Street",
    "My name is Sarah",
    "Ground floor, door 1",
]
CONTINUE_CA = ["Sí, correcte", "Carrer Gran número deu", "Tercer segona", "Em dic Jordi"]

OPENERS = {
    "es": [
        "",
        "",
        "Hola, ",
        "Buenas, ",
        "Mire, ",
        "Llamo porque ",
        "Oiga, ",
        "Pues verá, ",
        "Buenos días, ",
    ],
    "en": ["", "", "Hi, ", "Hello, ", "Look, ", "I'm calling because ", "Well, "],
    "ca": ["", "Hola, ", "Miri, ", "Truco perquè "],
}
CLOSERS = {
    "es": [
        "",
        "",
        "",
        ", a ver si lo pueden mirar",
        ", es urgente",
        ", desde ayer",
        ", ¿pueden venir?",
    ],
    "en": ["", "", ", can someone come?", ", since yesterday", ", please"],
    "ca": ["", "", ", si us plau", ", des d'ahir"],
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def _gold_texts() -> list[str]:
    texts: list[str] = []
    for path in sorted(EVALS.glob("gold_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                texts.append(_norm(row.get("request") or row.get("utterance") or ""))
    return texts


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[:2] not in ("¿C", "¿Q") else text


def _vary(text: str, lang: str, rng: random.Random, copies: int) -> list[str]:
    out = {text}
    for _ in range(copies * 3):
        if len(out) > copies:
            break
        opener = rng.choice(OPENERS[lang])
        closer = rng.choice(CLOSERS[lang])
        body = _lower_first(text) if opener else text
        out.add(f"{opener}{body}{closer}")
    return sorted(out)


def build(seed: int, copies: int = 2) -> list[dict[str, object]]:
    rng = random.Random(seed)
    rows: list[dict[str, object]] = []
    for lang, table in (("es", ES), ("en", EN), ("ca", CA)):
        for text, category, urgency, work_type in table:
            for variant in _vary(text, lang, rng, copies):
                rows.append(
                    {"text": variant, "lang": lang, "intent": "continue", "category": category,
                     "urgency": urgency, "work_type": work_type}
                )  # fmt: skip
    for lang, intents in (("es", INTENTS_ES), ("en", INTENTS_EN), ("ca", INTENTS_CA)):
        for text, intent in intents:
            rows.append({"text": text, "lang": lang, "intent": intent})
    for lang, answers in (("es", CONTINUE_ES), ("en", CONTINUE_EN), ("ca", CONTINUE_CA)):
        for text in answers:
            rows.append({"text": text, "lang": lang, "intent": "continue"})
    gold = _gold_texts()
    kept = [
        r for r in rows
        if not any(SequenceMatcher(None, _norm(str(r["text"])), g).ratio() >= 0.85 for g in gold)
    ]  # fmt: skip
    rng.shuffle(kept)
    return kept


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--copies", type=int, default=2, help="augmented variants per incident")
    ap.add_argument("--out", default=str(HERE / "train.jsonl"))
    args = ap.parse_args()
    rows = build(args.seed, args.copies)
    Path(args.out).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    print(f"{len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
