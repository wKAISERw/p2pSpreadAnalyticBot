# Збірка статики та роздача її через nginx, який заодно проксює /api
# на контейнер сканера. Ключ API підставляє проксі — у бандл він не потрапляє.

FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# У продакшені фронтенд ходить на той самий origin, а nginx уже перекидає
# запити на бекенд. Тому шлях відносний.
ARG VITE_API_URL=/api/v1
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build


FROM nginx:1.27-alpine

# Офіційний образ сам проганяє /etc/nginx/templates/*.template через envsubst
# при старті. Фільтр лишає підстановку тільки нашим змінним, щоб nginx-ові
# $host / $uri / $scheme дожили до конфіга неушкодженими.
ENV NGINX_ENVSUBST_FILTER=^(ARBIX_|SCANNER_)

COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
