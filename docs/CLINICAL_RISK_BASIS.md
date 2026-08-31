# Motor de revisión clínica v1.4

Este motor asigna **prioridad operativa de revisión**, no una probabilidad de suicidio,
recaída o patología dual. Los niveles N0–N4, las ventanas de vigencia y los umbrales
estadísticos son reglas del producto; no son puntos de corte clínicamente validados.
No deben decidir por sí solos alta, acceso a tratamiento o intervención de emergencia.

## Base clínica y límites

- [C-SSRS ambulatorio, Columbia, 2026](https://cssrs.columbia.edu/wp-content/uploads/C-SSRS-Screener-with-Triage-Points-for-outpatientambulatory-2026.pdf): diferencia deseo de morir, pensamiento activo, método, intención, plan y conducta; requiere preguntas y temporalidad explícitas. Una inferencia textual no equivale a una respuesta administrada.
- [SAFE-T, SAMHSA, 2024](https://library.samhsa.gov/sites/default/files/safet-flyer-pep24-01-036.pdf): indagación directa, factores de riesgo/protección, juicio clínico, plan de seguridad y documentación. Los protectores no neutralizan una señal aguda.
- [BAM, instrucciones VA](https://www.mentalhealth.va.gov/healthcare-providers/docs/VA_OMH_The_Brief_Addiction-Monitor_Instuctions_508.pdf): seguimiento de consumo, riesgo y protección. Incluye sueño, malestar, craving, confianza en abstinencia y contexto. VA no establece un total psicométrico refinado ni cortes de riesgo/protección. Horas de sueño y autoeficacia general diarias no equivalen a los ítems BAM ni a sus periodos de referencia.
- [ASSIST, OMS](https://www.afro.who.int/sites/default/files/2017-06/9789241599382_eng.pdf): cribado de problemas por sustancias mediante su cuestionario completo y puntuación por sustancia. No predice recaída diaria y sus cortes no se aplican a datos de chat o autochecking.
- [SAMHSA TIP 42](https://library.samhsa.gov/sites/default/files/SAMHSA_Digital_Download/PEP20-02-01_004.pdf): evaluación integrada de seguridad, consumo, síntomas mentales, trauma y funcionamiento en trastornos concurrentes; no aporta una suma universal validada de riesgo dual.
- [NICE NG225, 1.6](https://www.nice.org.uk/guidance/ng225/chapter/recommendations#risk-assessment-tools-and-scales): no usar escalas/categorías globales para predecir suicidio ni decidir tratamiento o alta tras autolesión.

## Reglas de seguridad del producto

1. Ideación indirecta/no explicitada activa y reciente genera como mínimo N3, **valoración clínica prioritaria pendiente**. No se transforma en ideación confirmada, intención o plan.
2. Una señal reciente no refutada conserva prioridad durante su ventana vigente aunque llegue después un texto neutro. Se guardan los identificadores de las dos fuentes: análisis actual y señal que impulsa la alerta.
3. La ideación directa y las declaraciones críticas conservan la prioridad superior existente de revisión urgente. La evaluación clínica determina peligro inmediato y actuación; un flag de IA no establece por sí mismo diagnóstico o emergencia confirmada.
4. Los cambios estadísticos, incluso concurrentes con rumiación/sueño, alcanzan revisión profesional N3; no se interpreta esa suma como emergencia suicida N4.
5. Las señales caducadas no se convierten en emergencia actual por su sola existencia. Las alertas abiertas se mantienen en el flujo de adjudicación humana. Refutar una inferencia excluye esa señal, sin borrar el historial.
6. Los protectores y las mejoras no rebajan las reglas de ideación ni cancelan declaraciones críticas.

## Corrección del score estructural

La versión anterior calculaba `max(0, 1 - mean(abs(z))/3)`: todo cambio grande terminaba en cero, incluidas mejoras, y una variabilidad casi nula magnificaba las desviaciones.

`structural-v2` conserva la similitud descriptiva, pero usa `1/(1+mean(abs(z)))`, sin recorte a cero. El denominador de cada z es `max(DE poblacional basal, mínimo técnico)`: 1 punto para ánimo, craving invertido y autoeficacia; 0,5 horas para sueño. Son estabilizadores técnicos, no umbrales clínicos.

Se calcula aparte el componente para revisión: `mean(max(-z,0))` en ánimo, craving invertido y autoeficacia; sueño aporta `abs(z)` porque aumentar horas no siempre implica mejora. Las cuatro variables participan en el denominador, incluidos ceros. No hay compensación entre mejoras y cambios adversos.

Las bandas usan límites de desviación 1,2 y 1,95; se documentan como operativas. La persistencia del motor lee la banda del componente de revisión y no reutiliza puntuaciones antiguas que confundían mejoras con deterioro. Faltan datos o alguna variable necesaria: resultado no evaluable (`null`), nunca cero imputado. Una base antigua conservada por escasez de datos queda marcada.

El historial previo se conserva con su fórmula y versión. Las nuevas evaluaciones no reescriben decisiones históricas. La similitud estructural deja de rellenar el campo de confianza clínica.

## Estadística diaria

El panel completo de estadísticas, score e inferencias corresponde exclusivamente
al profesional autorizado. El paciente sólo ve una gráfica sencilla de ánimo,
craving, autoeficacia y sueño con las respuestas de su último check-in de cada
día. Su endpoint de historial no carga ni devuelve análisis o estadísticas.

Fechas en `Europe/Madrid`, con cambio horario; timestamps antiguos sin zona se interpretan como UTC según el contrato de la aplicación. Se promedian observaciones dentro de cada fecha antes del resumen y las correlaciones, de forma que un día con muchos registros no pese más.

Se informan ánimo, sueño, autoeficacia, craving, valencia **negativa** media (la variable realmente disponible), rumiación, urgencia, ambivalencia, ideación directa/indirecta, crisis de consumo, comparación con la expresión habitual y todas las variables numéricas/categóricas psicosociales persistidas. Los textos explicativos siguen siendo evidencia cualitativa, no números inventados.

Las variables booleanas se resumen por presencia y frecuencia; la ideación no se diluye mediante una media. Cada media tiene denominador, DE muestral cuando procede y mínimos/máximos. Pearson requiere al menos tres pares completos y variación en ambas variables; no implica causalidad ni predicción clínica. Se excluyen refutaciones, se evita contar dos veces el mismo texto y se conserva la procedencia temporal.

## Validación clínica pendiente

La revisión documental y los tests de software no validan clínicamente un algoritmo. Antes de usar estos niveles para decisiones asistenciales se necesita revisión de profesionales responsables, protocolo local, administración real de instrumentos cuando corresponda y evaluación prospectiva de calibración, sensibilidad, especificidad y falsos positivos/negativos.

## Actualización de resultados existentes

El operador puede ejecutar `python -m app.maintenance.refresh_risk_v14` para
previsualizar con reversión completa y añadir `--apply` para guardar una nueva
evaluación por paciente activo con datos. Como alternativa de despliegue,
`RISK_V14_MAINTENANCE=apply` ejecuta primero la previsualización y después la
corrección durante el arranque; restablecer `off` tras verificarla.

Cada paciente se actualiza en una transacción, con bloqueo y marcador de
auditoría para poder reanudar sin duplicados. No reescribe evaluaciones ni
check-ins antiguos, no cierra alertas y no invoca modelos de lenguaje. Las
alertas nuevas quedan visibles para revisión, sin enviar notificaciones
externas durante este mantenimiento. El flujo normal conserva sus avisos.
