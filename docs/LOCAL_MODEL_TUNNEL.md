# Modelo local (LM Studio) → PsychDeep en Render

Render no puede conectar directamente con `127.0.0.1` ni con una IP privada de tu casa. Para usar el modelo del portátil desde PsychDeep alojado, el diseño estable es un **Cloudflare Tunnel remoto/gestionado** con hostname fijo y autenticación propia de LM Studio.

```text
móvil / PC -> PsychDeep -> psychdeep-api (Render)
                               |
                               | HTTPS
                               v
                    https://<hostname>/v1
                               |
                         Cloudflare Tunnel
                               |
                               v
                       LM Studio :1234
```

El navegador nunca accede directamente a LM Studio. El backend de PsychDeep es el cliente del modelo.

## Diseño recomendado

1. LM Studio ejecuta el servidor OpenAI-compatible en el portátil.
2. **Require Authentication** permanece activado en LM Studio y se usa un token de API independiente.
3. Un túnel Cloudflare **remotely managed** publica únicamente `http://host.docker.internal:1234`.
4. El conector `cloudflared` se autentica con un tunnel token guardado solo en:

```text
ops/local/secrets/cloudflare-tunnel-token.txt
```

5. El hostname estable pertenece a una zona que ya esté gestionada por Cloudflare.
6. PostgreSQL/SymmetricDS no usan este túnel.

No es necesario ni deseable mover automáticamente `bfab.io` a Cloudflare solo para esta función. Si esa zona sigue en otro proveedor, usa otra zona ya administrada en Cloudflare o configura conscientemente una cuando quieras un hostname estable.

## Arranque

Después de crear el túnel remoto en Cloudflare Zero Trust y configurar su Public Hostname para apuntar al servicio LM Studio:

```powershell
.\ops\local\start-tunnel.ps1
```

El script solicita el tunnel token la primera vez y lo guarda en el directorio de secretos ignorado por Git. Docker ejecuta `cloudflared tunnel --no-autoupdate run --token-file ...` mediante el perfil `tunnel` de `docker-compose.offline.yml`.

En PsychDeep, la configuración del modelo alojado debe usar:

```text
Provider: OpenAI compatible
Base URL: https://<hostname-estable>/v1
API key:  <token API de LM Studio>
Model:    <identificador exacto cargado por LM Studio>
```

`llm_endpoint_configs` está excluida deliberadamente de la sincronización de PostgreSQL: el endpoint cloud y el endpoint local son configuraciones de nodo distintas.

## Uso local

Cuando PsychDeep se ejecuta localmente en Docker no necesita Cloudflare:

```text
Base URL: http://host.docker.internal:1234/v1
```

Esto evita sacar texto clínico a Internet cuando estás usando la aplicación local.

## Quick Tunnel: solo diagnóstico

Un Quick Tunnel `*.trycloudflare.com` puede seguir siendo útil para comprobar en minutos que Render alcanza LM Studio, pero no debe ser la configuración persistente:

- la URL cambia al reiniciar;
- no proporciona por sí solo una identidad estable del servicio;
- obliga a reconfigurar PsychDeep cada vez.

Si se usa temporalmente, LM Studio debe seguir exigiendo su API token.

## Qué no hacer

| Configuración | Problema |
|---|---|
| `http://127.0.0.1:1234/v1` desde Render | apunta al loopback del contenedor remoto |
| `http://192.168.x.x:1234/v1` desde Render | no existe ruta desde Render a la LAN |
| `http://host.docker.internal:1234/v1` desde Render | solo tiene sentido en Docker del propio portátil |
| abrir 1234 en el router | expone directamente el servidor y evita el control HTTPS del túnel |
| tunelizar PostgreSQL local | no es parte del diseño; la sincronización DB sale del portátil hacia Supabase |
| guardar tunnel/API tokens en Git | compromete el endpoint local |

## Disponibilidad

Si el portátil, LM Studio o el túnel están apagados, el endpoint local remoto no está disponible. PsychDeep debe conservar Claude/Anthropic como configuración/fallback cloud cuando corresponda. Para funcionamiento sin Internet utiliza la instancia local de PsychDeep, PostgreSQL local y LM Studio local.

## Elección del modelo

No fijes en el repositorio un modelo concreto. La elección depende de GPU/VRAM, RAM y espacio reales del equipo. Primero se inspecciona el hardware y después se descarga en LM Studio el modelo/quantization que ofrezca el mejor equilibrio clínico entre calidad, contexto y latencia. Los pesos tampoco se versionan en Git.
