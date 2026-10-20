# Alcance del MVP

Propuesta para validación del propietario. Empresa ETO peruana; menos de diez usuarios internos. Ejecución en taller, en instalaciones del contratante o mixta.

## Incluido

- Clientes, contactos, oportunidades, cotizaciones, actividades y seguimiento.
- Proyectos, tareas, fabricación y etapas de instalación con ubicación.
- Materiales, movimientos y consumo por proyecto; presupuesto, costos reales y desviaciones.
- Condiciones contractuales, responsables, hitos, conformidades, facturas y cobranzas.
- Roles internos, permisos por documento/proyecto y seguimiento de cambios.
- Tableros de costos, rentabilidad, bloqueos, hitos, facturas y proyección básica de cobros.

Se configurará primero el estándar; una brecha documentada justificará una extensión.

## Recorrido principal

Cotización aprobada -> pedido y proyecto -> línea base y revisión técnica -> compras/materiales -> taller/obra -> evidencia de conformidad -> hito habilitado -> factura -> pago y conciliación.

El cronograma de pagos expresa importes y vencimientos. Un hito técnico expresa condiciones para facturar o cobrar; no se consideran equivalentes.

Administración, SSOMA, Calidad, Oficina Técnica, Producción, Residente y Planeamiento son áreas del contratante cuando participan en sus aprobaciones. Los responsables externos se registrarán como contactos y evidencias, sin otorgarles cuentas internas automáticamente.

## Fuera del MVP inicial

Kubernetes, microservicios, Azure, modelos de IA, portal de proveedores/clientes y aprobación externa por enlaces públicos. La integración fiscal electrónica no se presupone incluida: requiere alcance y validación explícitos.

## Definiciones que faltan del negocio

- Ejemplos de contrato, cotización, valorización y conformidad; criterios reales de facturabilidad.
- Unidades y monedas; costos directos, indirectos, comprometidos, desperdicio y retrabajo.
- Manejo de cambios de alcance, adelantos, retenciones y entregas parciales.
- Reglas de revisión y de invalidación de aprobaciones anteriores.
- Método de avance y tratamiento de etapas de taller/obra.
- Validación contable de configuración peruana y alcance de futuras integraciones fiscales.

Estas preguntas no impiden inspeccionar fuentes. Deben cerrarse antes de implementar las reglas afectadas.

## Aceptación funcional propuesta

Un proyecto representativo de cada modalidad debe poder seguirse desde cotización hasta cobro, con documentos relacionados, responsables, evidencia y costos conciliables. Un cambio de alcance debe conservar la línea base anterior. Una factura cobrada parcialmente debe mostrar saldo pendiente sin duplicar el ingreso previsto del hito asociado.
