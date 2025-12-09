# Guía de Uso e Integración: Conector Logístico 3PL para Odoo 19

Este documento describe cómo configurar y utilizar el módulo `logistics_3pl_connector` para integrar Odoo con su operador logístico.

## 1. Configuración Inicial

Una vez instalado el módulo, debe configurar las credenciales de conexión con el 3PL.

1.  Vaya a **Inventario > Configuración > Ajustes**.
2.  Busque la sección **3PL Integration**.
3.  Complete los siguientes campos:
    *   **3PL API URL:** La dirección base del API proporcionada por su operador (ej. `https://api.su-operador-logistico.com`).
    *   **3PL API Key:** La clave de seguridad (token) para autenticarse. Esta misma clave se usará para validar los webhooks entrantes.
    *   **3PL Warehouse:** (Opcional) Seleccione un almacén específico. Si se configura, solo los albaranes que salgan de este almacén serán enviados al 3PL. Si se deja vacío, se procesarán todas las salidas.
    *   **Auto Send to 3PL:** Marque esta casilla si desea que los albaranes elegibles se envíen automáticamente al 3PL cuando se intenta validar. Si el envío es exitoso, el albarán pasa a estado "Waiting 3PL" y la validación se bloquea hasta recibir confirmación del 3PL (o usar "Force Validate").
    *   **Tracking URL Base:** URL base para construir el enlace de seguimiento cuando el 3PL solo envía el número de tracking. Por defecto: `https://www.example.com/odoo/`. El número de tracking se añadirá al final.

## 2. Flujo de Salida (Envíos al 3PL)

### Nuevo Flujo con Estado Intermedio

El módulo añade un estado intermedio `Waiting 3PL` al flujo nativo de Odoo:

```
Borrador → Confirmado → Listo (assigned) → [📤 Send to 3PL] → Waiting 3PL → [Webhook shipped] → Hecho (done)
```

### Proceso paso a paso:
1.  **Confirme la venta** y vaya al albarán generado.
2.  El albarán estará en estado **"Listo"** (assigned).
3.  Haga clic en el botón **📤 Send to 3PL** (disponible solo si el albarán es elegible según el almacén configurado y no ha sido enviado previamente o está en error).
4.  El estado cambia a **"Waiting 3PL"** - el inventario aún NO se ha movido.
5.  Cuando el 3PL confirme el envío (webhook con `status: shipped`), el albarán se **valida automáticamente** → estado **"Hecho"** (done).

### Botones disponibles:
*   **Send to 3PL:** Disponible cuando el albarán está en estado "Listo" (assigned), es elegible para 3PL (según el almacén configurado), y tiene estado 3PL "Not Sent" o "Error".
*   **Validar (Forzar):** Disponible en estado "Waiting 3PL" para validar manualmente sin esperar la confirmación del 3PL. Muestra un diálogo de confirmación antes de proceder.
*   **Track Shipment:** Abre la URL de seguimiento cuando está disponible (solo visible cuando hay una URL de tracking configurada).

> **Nota:** El botón nativo "Validar" de Odoo NO está disponible mientras el albarán está en "Waiting 3PL". Si intenta validar directamente, recibirá un error indicando que debe usar "Force Validate" o esperar la confirmación del 3PL.

### Monitoreo de Estado
En cada albarán, la pestaña **3PL Logistics** muestra:
*   **3PL Order ID:** El identificador único devuelto por el sistema del operador.
*   **3PL Status:** (también visible como badge en la cabecera y lista)
    *   *Not Sent:* ⚪ Aún no enviado al 3PL.
    *   *Sent to 3PL:* 🔵 Enviado, esperando confirmación.
    *   *Shipped:* 🟢 Enviado al cliente (con tracking).
    *   *Error:* 🔴 Fallo en la comunicación.

### Payload Enviado al 3PL
Cuando se envía un albarán, Odoo realiza una petición `POST` a `{API_URL}/orders` con el siguiente formato:

```json
{
  "order_reference": "WH/OUT/00001",
  "partner_name": "Cliente Ejemplo S.L.",
  "partner_address": "Calle Mayor 1, 08001 Barcelona, España",
  "lines": [
    {
      "product_code": "PROD-001",
      "product_name": "Producto Ejemplo",
      "quantity": 5.0
    }
  ]
}
```

Headers enviados:
*   `Content-Type: application/json`
*   `Authorization: Bearer <API_KEY>`

**Respuesta Esperada del 3PL:**
El 3PL debe responder con código HTTP 200 o 201 y un JSON con el campo `order_id`:
```json
{
  "order_id": "EXT-12345"
}
```

Este `order_id` se guarda en el campo **3PL Order ID** del albarán para referencia futura.

## 3. Flujo de Entrada (Webhooks)

El operador logístico debe configurar sus sistemas para enviar actualizaciones a Odoo cuando el estado del envío cambie (ej. cuando se genere la etiqueta de envío).

### Configuración para el 3PL
Proporcione la siguiente información a su equipo técnico o al proveedor 3PL:

*   **Endpoint URL:** `https://<su-dominio-odoo>/api/v1/3pl/webhook`
*   **Método:** `POST`
*   **Headers Requeridos:**
    *   `Content-Type: application/json`
    *   `Authorization: Bearer <Su-API-Key-Configurada-en-Odoo>`
*   **Payload JSON:**
    ```json
    {
      "order_id": "WH/OUT/00001",
      "tracking_number": "1Z999AA10123456784",
      "tracking_url": "https://tracking.example.com/1Z999AA10123456784",
      "status": "shipped"
    }
    ```

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `order_id` | string | ✅ Sí | Nombre exacto del albarán en Odoo |
| `tracking_number` | string | No | Número de seguimiento del transportista |
| `tracking_url` | string | No | URL completa de seguimiento. Si no se proporciona pero hay `tracking_number`, se construye automáticamente usando la **Tracking URL Base** configurada |
| `status` | string | No | Estado del envío. Valores permitidos: `shipped`, `delivered`, `completed`, `error`. Si se proporciona un valor diferente, el webhook rechazará la petición con error 400. Solo `shipped` activa la auto-validación del albarán cuando está en estado `waiting_3pl`. Los valores `delivered` y `completed` actualizan el estado 3PL a "shipped" pero no activan la auto-validación. |

### Respuestas del Webhook

**Éxito (200):**
```json
{"status": "success", "order_id": "WH/OUT/00001", "tracking_url": "https://tracking.example.com/odoo/1Z999AA10123456784"}
```

**Errores:**
| Código | Respuesta | Causa |
|--------|-----------|-------|
| 400 | `{"status": "error", "message": "Missing order_id"}` | Falta el campo `order_id` |
| 400 | `{"status": "error", "message": "Invalid status \"lele\". Allowed statuses are: shipped, delivered, completed, error"}` | El campo `status` contiene un valor no permitido |
| 400 | `{"status": "error", "message": "Order X is in state \"done\". Updates are only allowed for pickings in states: waiting_3pl, assigned"}` | El albarán está en un estado no permitido (solo se aceptan actualizaciones cuando está en `waiting_3pl` o `assigned`) |
| 401 | `{"status": "error", "message": "Unauthorized"}` | Token inválido o no proporcionado |
| 404 | `{"status": "error", "message": "Order X not found"}` | El albarán no existe en Odoo |
| 500 | `{"status": "error", "message": "..."}` | Error interno del servidor |

### Resultado en Odoo
Cuando Odoo recibe un webhook válido:
1.  Valida la autenticación usando el header `Authorization: Bearer <API_KEY>`.
2.  Busca el albarán por su nombre (`order_id`).
3.  **Valida el estado del albarán** - Solo acepta actualizaciones cuando el albarán está en estado `waiting_3pl` o `assigned`. Rechaza con error 400 si está en otro estado (ej. `done`, `cancel`, `draft`).
4.  Actualiza el campo **Tracking Number** (`x_3pl_tracking_ref`) con el número de tracking (si se proporciona).
5.  Actualiza el campo **Tracking URL** (`x_3pl_tracking_url`) con la URL proporcionada o construida automáticamente usando la "Tracking URL Base" si solo se proporciona `tracking_number`.
6.  Cambia el **3PL Status** a `shipped` si el status recibido es `shipped`, `delivered` o `completed`, o a `error` si el status es `error`.
7.  Registra una nota en el chatter del albarán con enlace clickable al tracking (autor: OdooBot).
8.  El botón **🔗 Track Shipment** aparece en la cabecera del albarán cuando hay una URL de tracking disponible.
9.  **Auto-validación:** Si el status es `shipped` (exactamente, no `delivered` ni `completed`) y el albarán está en `waiting_3pl`, se valida automáticamente (cambia a estado `done`).

## 4. Pruebas

### Probar el Webhook con cURL
```bash
# Solo con tracking_number (la URL se construye automáticamente)
curl -X POST https://su-dominio-odoo.com/api/v1/3pl/webhook \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer su-api-key-aqui" \
  -d '{
    "order_id": "WH/OUT/00001",
    "tracking_number": "1Z999AA10123456784",
    "status": "shipped"
  }'

# Con tracking_url personalizada
curl -X POST https://su-dominio-odoo.com/api/v1/3pl/webhook \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer su-api-key-aqui" \
  -d '{
    "order_id": "WH/OUT/00001",
    "tracking_number": "1Z999AA10123456784",
    "tracking_url": "https://tracking.dhl.com/1Z999AA10123456784",
    "status": "shipped"
  }'

```

### Ver logs del webhook
Si usa Kubernetes:
```bash
kubectl logs -n odoo deployment/odoo | grep "3PL Webhook"
```

## 5. Solución de Problemas

| Problema | Causa | Solución |
|----------|-------|----------|
| Estado "Error" después de enviar | Fallo de conexión con el API del 3PL | Revise los mensajes en el chatter del albarán. Verifique URL y API Key en Ajustes. |
| Webhook devuelve 401 | Token incorrecto | Asegúrese de que el header `Authorization: Bearer <token>` coincida exactamente con la API Key configurada en Odoo. |
| Webhook devuelve 404 | Albarán no encontrado | Verifique que `order_id` coincida *exactamente* con el nombre del albarán (ej. `WH/OUT/00123`). |
| Webhook no llega a Odoo | Red/Firewall bloqueando | Verifique que el Ingress de Kubernetes permita tráfico externo hacia `/api/v1/3pl/webhook`. |
| Botón "Send to 3PL" no aparece | Condiciones no cumplidas | El botón solo aparece cuando: (1) el albarán es de tipo salida (`outgoing`), (2) está en estado "Listo" (assigned), (3) pertenece al almacén configurado (o no hay almacén configurado), (4) tiene status 3PL "Not Sent" o "Error". |

## 6. Notas Técnicas

*   El webhook usa `auth='none'` para evitar requerir sesión de Odoo, permitiendo llamadas desde sistemas externos.
*   La autenticación se realiza mediante el header `Authorization: Bearer <API_KEY>` que debe coincidir exactamente con la API Key configurada en Odoo.
*   Los mensajes en el chatter son publicados por **OdooBot** (usuario root) ya que el webhook no tiene usuario asociado.
*   El módulo no crea nuevos modelos, solo extiende `stock.picking` y `res.config.settings`.
*   El estado `waiting_3pl` se inserta antes de `done` en la secuencia de estados, permitiendo que aparezca en el statusbar entre "assigned" y "done".
*   Cuando se habilita "Auto Send to 3PL", los albaranes elegibles se envían automáticamente al validar, pero la validación se bloquea hasta recibir confirmación del 3PL (a menos que se use "Force Validate").
