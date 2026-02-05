# Guía de Uso e Integración: Conector Logístico 3PL para Odoo 19

Este documento describe cómo configurar y utilizar el módulo `logistics_3pl_connector` para integrar Odoo con su operador logístico.

## 1. Configuración Inicial

Una vez instalado el módulo, debe configurar las credenciales de conexión con e-Transport.

1.  Vaya a **Inventario > Configuración > Ajustes**.
2.  Busque la sección **e-Transport 3PL Integration**.
3.  Complete los siguientes campos:

### Conexión API
*   **3PL API URL:** La dirección base del API (ej. `https://e-transport.es/api`).
*   **3PL API Key:** El token X-API-Key proporcionado por e-Transport. Esta misma clave se usará para validar los webhooks entrantes.
*   **3PL Warehouse:** (Opcional) Seleccione un almacén específico. Si se configura, solo los albaranes de este almacén serán enviados al 3PL. Si se deja vacío, se procesarán todas las salidas.

### Parámetros e-Transport
*   **ShipmentType (eCommerce):** Código para pedidos web (por defecto: `E` = Entrega).
*   **ShipmentType (Internal):** Código para pedidos internos/manuales (por defecto: `M` = Movimiento).
*   **ServiceType:** Código de servicio (ej: `ND_3H` para ventana de entrega de 3 horas).
*   **Default Temperature:** Temperatura por defecto para mercancías (`AM` = Ambiente, `FR` = Frío, `CO` = Congelado).

### Automatización
*   **Auto Send to 3PL:** Marque esta casilla si desea que los albaranes elegibles se envíen automáticamente al intentar validar. Si el envío es exitoso, el albarán pasa a estado "Waiting 3PL" y la validación se bloquea hasta recibir confirmación del 3PL.
    *   **Web Orders Only:** (Sub-opción) Si está activado, el auto-envío solo aplica a pedidos web. Los pedidos manuales se pueden enviar con el botón "Enviar a e-Transport".
*   **Allow Resend to 3PL:** Permite reenviar pedidos que ya fueron enviados. Útil para testing o cuando hay problemas. ⚠️ Usar con precaución: puede crear duplicados si e-Transport no actualiza por ExternalRef.

### Tracking
*   **Tracking URL Base:** URL base para construir enlaces de seguimiento. Por defecto: `https://e-transport.es/tracking/`. El número de tracking se añadirá al final.
*   **Webhook User:** (Recomendado) Usuario dedicado para operaciones automáticas de webhook. Por seguridad, cree un usuario con solo permisos de Inventario. Si no se configura, se usará OdooBot.

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
*   **Enviar a e-Transport:** Disponible cuando el albarán está en estado "Listo" (assigned), es elegible para 3PL (según el almacén configurado), y tiene estado 3PL "Not Sent" o "Error".
*   **Reenviar a e-Transport (retry):** Disponible cuando el estado 3PL es "Error" y el albarán NO está en estado "assigned". Permite reintentar el envío tras un fallo.
*   **Reenviar a e-Transport (resend):** Disponible solo si "Allow Resend to 3PL" está habilitado y el pedido ya fue enviado. Muestra confirmación de advertencia sobre posibles duplicados.
*   **Actualizar Tracking:** Disponible cuando el estado 3PL es "Sent" o "Shipped". Consulta el estado actual desde la API de e-Transport (`GET /tms/tracking/{ref}`).
*   **Validar (Forzar):** Disponible en estado "Waiting 3PL" para validar manualmente sin esperar la confirmación del 3PL. Muestra un diálogo de confirmación antes de proceder.
*   **Ver Tracking:** Abre la URL de seguimiento en una nueva pestaña. Solo visible cuando hay una URL de tracking disponible.

> **Nota:** El botón nativo "Validar" de Odoo NO está disponible mientras el albarán está en "Waiting 3PL". Si intenta validar directamente, recibirá un error indicando que debe usar "Validar (Forzar)" o esperar la confirmación del 3PL vía webhook.

### Monitoreo de Estado
En cada albarán, la pestaña **e-Transport 3PL** muestra:
*   **3PL Order ID:** El identificador único devuelto por e-Transport (TMS ID).
*   **3PL Status:** (también visible como badge en la cabecera y lista)
    *   *Not Sent:* ⚪ Aún no enviado.
    *   *Sent to 3PL:* 🔵 Enviado, esperando confirmación.
    *   *Shipped:* 🟠 En tránsito.
    *   *Delivered:* 🟢 Entregado.
    *   *Error:* 🔴 Fallo en la comunicación.
*   **e-Transport State:** Estado actual reportado por e-Transport (tras consultar tracking).
*   **Tracking Number:** Número de seguimiento del transportista.
*   **Tracking URL:** Enlace clickable al seguimiento.

### Payload Enviado a e-Transport
Cuando se envía un albarán, Odoo realiza una petición `POST` a `{API_URL}/tms/import-data` con el formato específico de e-Transport:

```json
{
  "Orders": [
    {
      "ExternalRef": "WH/OUT/00001",
      "ShipmentType": "E",
      "ServiceType": "ND_3H",
      "Legs": [
        {
          "UnLoadName": "Cliente Ejemplo S.L.",
          "UnLoadAddress": "Carrer Major 123",
          "UnLoadCity": "Barcelona",
          "UnLoadZip": "08015",
          "UnLoadCountry": "ES",
          "UnLoadDate": "2025-03-15",
          "UnLoadStartTime": "08:00",
          "UnLoadEndTime": "11:00",
          "UnLoadTel": "+34612345678",
          "UnLoadEmail": "cliente@example.com",
          "Goods": [
            {
              "Packs": 3,
              "PacksTypeID": "prod-001",
              "PacksDescription": "Producto Congelado",
              "PacksTemperature": "FR",
              "GrossWeight": 25.0,
              "Cube": 0.3,
              "Parcels": []
            }
          ]
        }
      ]
    }
  ]
}
```

**Mapeo de campos Odoo → e-Transport:**

| Campo Odoo | Campo e-Transport | Notas |
|------------|-------------------|-------|
| `picking.name` | `ExternalRef` | Referencia única del albarán |
| Config / `x_is_web_order` | `ShipmentType` | E (eCommerce) o M (interno) |
| Config | `ServiceType` | Configurable (ej: ND_3H) |
| `partner.name` | `UnLoadName` | |
| `partner.street` | `UnLoadAddress` | |
| `partner.city` | `UnLoadCity` | |
| `partner.zip` | `UnLoadZip` | |
| `partner.country_id.code` | `UnLoadCountry` | |
| `partner.phone/mobile` | `UnLoadTel` | |
| `partner.email` | `UnLoadEmail` | |
| `scheduled_delivery_date` | `UnLoadDate` | (Opcional) Formato YYYY-MM-DD |
| `delivery_time_slot_id.start_hour` | `UnLoadStartTime` | (Opcional) Formato HH:MM |
| `delivery_time_slot_id.end_hour` | `UnLoadEndTime` | (Opcional) Formato HH:MM |
| `move.product_uom_qty` | `Packs` | Cantidad |
| `product.default_code` o nombre | `PacksTypeID` | Si no hay código, se genera del nombre |
| `product.name` | `PacksDescription` | |
| Config | `PacksTemperature` | AM/FR/CO |
| `product.weight * qty` | `GrossWeight` | En kg |
| `product.volume * qty` | `Cube` | (Opcional) En m³ |

Headers enviados:
*   `Content-Type: application/json`
*   `X-API-Key: <API_KEY>` (nota: NO usa Bearer, usa X-API-Key directamente)

**Respuesta Esperada de e-Transport:**
```json
{
  "status": "success",
  "orders_created": 1,
  "orders_updated": 0,
  "warnings": [],
  "errors": [],
  "mapping": {
    "orders": {
      "WH/OUT/00001": 4589
    }
  }
}
```

El TMS ID (`4589` en el ejemplo) se extrae del mapping y se guarda en el campo **3PL Order ID**.

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
*   **Autenticación webhook:** Se realiza mediante el header `Authorization: Bearer <API_KEY>` que debe coincidir exactamente con la API Key configurada en Odoo.
*   **Autenticación API (salida):** Se usa el header `X-API-Key: <API_KEY>` para llamadas a e-Transport.
*   El módulo no crea nuevos modelos, solo extiende `stock.picking` y `res.config.settings`.
*   El estado `waiting_3pl` se inserta antes de `done` en la secuencia de estados, permitiendo que aparezca en el statusbar entre "assigned" y "done".
*   **Auto-envío:** Cuando se habilita "Auto Send to 3PL", los albaranes elegibles se envían automáticamente al validar, pero la validación se bloquea hasta recibir confirmación del 3PL (a menos que se use "Validar (Forzar)").
*   **Computed fields:** `x_3pl_eligible`, `x_3pl_can_resend`, y `x_is_web_order` son campos calculados que determinan la visibilidad de botones y comportamiento.

### 6.1. Configuración de Seguridad del Webhook

⚠️ **Recomendación de Seguridad**: Por defecto, el webhook utiliza **OdooBot** (superusuario) para las operaciones automáticas de validación. Esto NO es la mejor práctica porque OdooBot tiene permisos elevados.

**Mejor práctica**: Crear un usuario dedicado con permisos mínimos:

1.  **Crear un usuario dedicado** (ej: "3PL Webhook User")
2.  **Asignar solo los grupos necesarios**:
    *   Inventario / Usuario
    *   (Opcionalmente) Ventas / Usuario si se necesita acceso a órdenes de venta
3.  **Configurar el usuario** en: *Inventario → Configuración → e-Transport 3PL Integration → Tracking → Webhook User*

Esto sigue el principio de **mínimos privilegios**: si el webhook es comprometido, el atacante solo tendría acceso limitado en lugar de control total del sistema.

### 6.2. Consulta de Tracking Manual

El botón "Actualizar Tracking" llama a `GET /tms/tracking/{external_ref}` con los siguientes parámetros:
*   `include_traceability: true`
*   `include_packs: true`
*   `traceability_limit: 10`

La respuesta actualiza:
*   `x_3pl_current_state`: Estado actual de e-Transport
*   `x_3pl_status`: Mapea estados (delivered/completed/done/entregado → `delivered`, in_transit/on_route/en_ruta/shipped/enviado → `shipped`)
*   `x_3pl_tracking_url`: Se construye automáticamente si se configura la URL base

Los eventos de traceability se muestran en el chatter del albarán.
