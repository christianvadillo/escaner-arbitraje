# Guion de 3 minutos — escáner de arbitraje

Notas para contar este proyecto en una entrevista. La historia tiene un final poco común y ese
es justo el punto: el proyecto concluyó que su propia idea no era buena, y eso se cuenta como
resultado, no como fracaso.

---

## El guion (≈3 min)

**1. La idea y por qué es sospechosa (25 s).**
Comprar liquidaciones en tiendas mexicanas y revenderlas en Mercado Libre. La idea es fácil de
creer: ves 30 % de diferencia entre el precio de liquidación y el de Mercado Libre y parece
dinero gratis. Yo no quería otra herramienta que me enseñara brechas brutas bonitas; quería
saber si después de todos los costos quedaba algo.

**2. Lo que descubrí, que es el resultado principal (45 s).**
No queda casi nada. Comisión de 13 a 17.5 %, envío que paga el vendedor arriba de $299,
retenciones de ISR e IVA del régimen de plataformas, empaque, una reserva por devoluciones y el
costo de tener capital parado. Con eso, un descuento del 20 al 25 % queda en punto de
equilibrio, y hacen falta 30 a 35 % para llegar a un ROI que valga la pena. De once ofertas con
descuentos realistas, sobreviven dos. Y ese número no salió de una opinión: salió del mismo
motor de costos que había escrito, corriendo sobre datos que calibré para que fueran realistas.

**3. La parte técnica más interesante (50 s).**
El emparejamiento. No hay identificador común entre una liquidación y una publicación de
Mercado Libre: solo títulos escritos por humanos distintos. Y el error asimétrico es brutal —
perderte una oferta cuesta una oportunidad, comprar el producto equivocado cuesta dinero de
verdad. Así que puse vetos duros antes de cualquier medida de parecido: código de barras, marca,
capacidad, variante, generación, paquetes, accesorios. La similitud solo opina sobre lo que
sobrevive a los vetos. Contra 41 pares que etiqueté a mano, precisión del 100 % en todos los
umbrales. El recall baja si subo el umbral, y eso es exactamente el intercambio que quiero.

**4. Cómo iba a decidir si arriesgar dinero (40 s).**
Cada oportunidad abre una posición de papel, una compra hipotética que se marca a mercado
durante días: se re-cotiza el precio real de Mercado Libre y se revisa si la oferta de origen
sigue viva. La regla de decisión la escribí antes de ver un solo dato: mínimo treinta posiciones
en al menos diez días distintos, y el intervalo de confianza de la utilidad por posición tiene
que estar completamente arriba de cero. Las estadísticas son las del problema, no las de
manual: supervivencia tipo Kaplan–Meier para la vida de una oferta, y bootstrap por bloques de
día para los intervalos, porque las oportunidades del mismo día están correlacionadas.

**5. Por qué está detenido (25 s).**
Por la conclusión económica, primero. Y porque los retailers grandes bloquean el acceso
automatizado; decidí no evadir esas protecciones, así que esas fuentes quedaron fuera por
política. Preferí parar ahí que seguir construyendo sobre una tesis que mi propia herramienta
había puesto en duda.

---

## Lo que suelen preguntar después

**"¿No es un fracaso entonces?"**
El objetivo era saber si había margen, no tener un bot corriendo. La herramienta respondió que
no, con números, antes de arriesgar capital. Un backtest que te ahorra perder dinero vale lo
mismo que uno que te lo hace ganar, y es mucho más frecuente. Lo que sí habría sido un fracaso
es haber ajustado los supuestos hasta que saliera rentable.

**"¿Cómo sabes que tus costos son los correctos?"**
Las tasas están verificadas contra el resumen de costos real de Mercado Libre y contra la ley de
ingresos vigente: ISR 2.5 % e IVA 8 % con RFC bajo el régimen de plataformas. Lo que sí es
estimación —la tabla de comisiones por categoría y el costo de envío por peso— está marcado
como tal en la salida de cada cálculo, y el programa dice explícitamente "conecta la API para la
real".

**"¿Algo te sorprendió en el modelado?"**
Sí, una no monotonía. Como el envío gratis obligatorio empieza en $299, vender a $298 te puede
dejar más utilidad que vender a $305. Cualquier optimizador que asuma que más precio es más
utilidad se equivoca en esa franja. Tiene una prueba dedicada y un aviso en el reporte.

**"¿Cómo evitas problemas legales o de bloqueo?"**
El cliente HTTP respeta `robots.txt` y los retrasos que pide cada sitio, cachea con peticiones
condicionales y se detiene ante un bloqueo en lugar de insistir. Los sitios que se defienden
activamente quedan fuera del alcance, y una de las fuentes permite uso personal pero no
redistribución, así que está anotado como límite si esto se convirtiera en producto.

**"¿Qué revisarías si lo retomaras?"**
La hipótesis de origen. En vez de liquidaciones amplias, buscaría nichos donde el precio de
Mercado Libre sea estructuralmente alto: categorías con poca competencia o productos donde el
vendedor promedio no optimiza. Pero eso es una tesis nueva, no un ajuste de parámetros a esta.

---

## Si piden ver código

- El motor de costos: `src/escaner/economics/profit.py` — donde muere la tesis, con el umbral
  de $299 y su no monotonía.
- Los vetos: `src/escaner/matching/score.py` — el diseño asimétrico, vetos antes que similitud.
- Las estadísticas del papel: `src/escaner/paper/stats.py` — Kaplan–Meier y bootstrap por bloques.
- La regla de decisión preregistrada: `docs/ESPECIFICACION.md`.
