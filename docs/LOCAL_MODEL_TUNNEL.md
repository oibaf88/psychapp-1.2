# Modelo local (LM Studio / Ollama) → API en Render

Render (Frankfurt) no puede abrir TCP a `127.0.0.1` ni a `192.168.x` de tu
PC. Docker no cambia eso. La vía **gratuita** es un túnel HTTPS de
Cloudflare: tu portátil publica el servidor local y FastAPI llama a esa URI
pública.

El navegador **no** habla con LM Studio. Habla con PsychDeep. PsychDeep
(en Render) es quien llama al modelo.

```
móvil / PC  →  https://psychapp.bfab.io  →  psychdeep-api (Render)
                                              ↓ HTTPS
                                    https://xxxx.trycloudflare.com/v1
                                              ↓
                                    LM Studio en tu PC :1234
```

## Requisitos

1. LM Studio (o Ollama / llama.cpp) con el servidor OpenAI-compatible
   escuchando en el PC.
2. [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/)
   (gratis, sin tarjeta). En Windows: `winget install Cloudflare.cloudflared`.

## Cada vez que quieras usar el modelo local

1. En LM Studio: **Developer → Local Server → Start**. Puerto **1234**.
   El modelo tiene que estar cargado.
2. En PowerShell, desde la raíz del repo:

   ```powershell
   .\start-model-tunnel.ps1
   ```

   Por defecto apunta a `http://127.0.0.1:1234`. Ollama: `.\start-model-tunnel.ps1 -Port 11434`.
3. cloudflared imprime una URL `https://….trycloudflare.com`.
4. En PsychDeep → Ajustes (cuenta `admin_clinical`):
   - proveedor **Modelo propio**
   - URI: `https://….trycloudflare.com/v1` (el sufijo `/v1` es obligatorio)
   - nombre del modelo: el que muestra LM Studio
   - **Probar el endpoint** → **Guardar**

Cuando apagues el túnel o el PC, Render deja de alcanzar el modelo. El
backend ignora un override inalcanzable y vuelve a Claude en lugar de
colgarse. Vuelve a Claude a mano con «Volver al modelo del despliegue»
si no vas a dejar el túnel encendido.

## Qué no funciona (y no lo intentes)

| URI | Por qué falla |
|---|---|
| `http://127.0.0.1:1234/v1` | Eso es el loopback de Frankfurt, no el de tu casa |
| `http://192.168.1.19:1234/v1` | No hay ruta desde la nube a tu LAN |
| `http://host.docker.internal:1234/v1` | En Render ese nombre es el propio contenedor |
| Abrir el puerto 1234 en el router | No es HTTPS; el backend lo rechaza desde la nube |

## Túnel con nombre fijo (también gratis)

La URL `trycloudflare.com` cambia en cada arranque. Si quieres
`https://lm.bfab.io/v1` estable:

1. Cuenta Cloudflare (plan Free) con `bfab.io`.
2. `cloudflared tunnel login`
3. `cloudflared tunnel create psychdeep-lm`
4. DNS CNAME `lm.bfab.io` → `<tunnel-id>.cfargotunnel.com`
5. `cloudflared tunnel route dns psychdeep-lm lm.bfab.io`
6. `cloudflared tunnel run --url http://127.0.0.1:1234 psychdeep-lm`

Eso sigue siendo gratis. El texto clínico viaja por el túnel: no lo dejes
abierto sin necesidad y no lo compartas.

## Privacidad

Un túnel público expone el API del modelo a quien tenga la URL. Cloudflare
Quick Tunnels no añaden autenticación. Úsalo solo mientras experimentas;
en producción cotidiana deja Claude (`ANTHROPIC_API_KEY` en Render).
