# Revisión de Arquitectura y Recomendaciones Serverless

La aplicación actual tiene una arquitectura clásica de contenedores con tres servicios principales:
1. **Frontend:** React + Vite (Servido mediante Nginx).
2. **Backend:** FastAPI (Python).
3. **Base de Datos:** PostgreSQL.

## 1. La Base de Datos
El proyecto **sí cuenta con una base de datos** relacional (PostgreSQL). Toda la persistencia (pacientes, profesionales, check-ins, diarios, análisis de riesgo, etc.) se almacena allí utilizando SQLAlchemy como ORM. Actualmente se despliega localmente en el contenedor `db` vía Docker Compose.
Para desplegar en la nube (serverless):
- **Opción Serverless:** Puedes utilizar una base de datos gestionada o verdaderamente *serverless* como **Neon (Postgres Serverless)** . También opciones en la nube como AWS RDS Proxy o Amazon Aurora Serverless (PostgreSQL-compatible) si buscas escalabilidad bajo demanda.
- Solo debes proveer la cadena de conexión en la variable de entorno `DATABASE_URL` al backend.

## 2. Despliegue del Frontend Serverless
El frontend construido (archivos estáticos) no necesita Nginx en un contenedor si se usa una solución Serverless.
- **Vercel, Netlify o Cloudflare Pages:** Son ideales para servir aplicaciones React/Vite.
- Solo requiere ejecutar `npm run build` y configurar las variables de entorno correctas durante el build (`VITE_API_BASE_URL` apuntando a tu backend desplegado).

## 3. Despliegue del Backend Serverless
FastAPI puede ejecutarse de manera *serverless* en la nube en lugar de ejecutarse continuamente en un contenedor de Docker:
- **AWS Lambda + API Gateway:** Usando un adaptador como **Mangum** (`pip install mangum`), puedes envolver tu aplicación FastAPI para que AWS Lambda la ejecute por petición.
- **Google Cloud Run:** Crea un contenedor de tu backend que escala a 0 (no cuesta cuando no se usa), acercándose al paradigma serverless.

## 4. LLMs Locales o APIs Adicionales
Actualmente la app integra el API de Anthropic (Claude).
Para soportar tu modelo local (Llama, Mistral, etc.) o usar OpenAI, crearemos en el código una abstracción para `openai` y un endpoint que puedas apuntar a cualquier proveedor compatible (por ejemplo, vLLM local, Ollama, o OpenAI puro).
