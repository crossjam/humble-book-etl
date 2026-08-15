# Stage 1: Build
FROM node:20-alpine AS builder

WORKDIR /app

ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# Copiar archivos de dependencias
COPY package*.json ./
RUN npm ci

# Copiar código fuente
COPY . .

# Build de producción
RUN npm run build

# Stage 2: Production
FROM nginx:alpine

# Copiar archivos build desde el stage anterior
COPY --from=builder /app/dist /usr/share/nginx/html

# Crear configuración de nginx personalizada para puerto 3002
RUN echo 'server { \
    listen 3002; \
    server_name localhost; \
    root /usr/share/nginx/html; \
    index index.html; \
    \
    # Configuración para archivos estáticos \
    # API reverse proxy for same-origin tailnet access
    location /api/ { \
        proxy_pass http://host.docker.internal:5002/; \
        proxy_http_version 1.1; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; \
        proxy_set_header X-Forwarded-Proto $scheme; \
    } \
    \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
    \
    # Cache para archivos estáticos \
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ { \
        expires 1y; \
        add_header Cache-Control "public, immutable"; \
    } \
    \
    # Gzip compression \
    gzip on; \
    gzip_vary on; \
    gzip_min_length 1024; \
    gzip_types text/plain text/css text/xml text/javascript application/x-javascript application/xml+rss application/json; \
}' > /etc/nginx/conf.d/default.conf

# Exponer puerto
EXPOSE 3002

# Comando por defecto
CMD ["nginx", "-g", "daemon off;"]

